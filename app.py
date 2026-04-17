import os
import re
import base64
import pytesseract
from flask import Flask, request, jsonify
from PIL import Image
from io import BytesIO

app = Flask(__name__)

# Hard reject anything over 10MB request body
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

# ── Verhoeff tables ───────────────────────────────────────────
D = [
    [0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
    [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
    [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
    [9,8,7,6,5,4,3,2,1,0],
]
P = [
    [0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
    [8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
    [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8],
]

def verhoeff_validate(number):
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = D[c][P[i % 8][int(digit)]]
    return c == 0

def extract_aadhaar_numbers(text):
    clean = re.sub(r'[^0-9]', '', text)
    found = []
    for i in range(len(clean) - 11):
        candidate = clean[i:i+12]
        if (
            len(candidate) == 12
            and candidate[0] not in ('0', '1')
            and verhoeff_validate(candidate)
            and candidate not in found
        ):
            found.append(candidate)
    return found

def compress_image(image):
    # Step 1: Grayscale — cuts RAM by 3x vs RGB
    image = image.convert('L')
    # Step 2: Aggressively cap size — 800px is enough for OCR on ID cards
    image.thumbnail((800, 800), Image.LANCZOS)
    return image


from werkzeug.exceptions import RequestEntityTooLarge

@app.errorhandler(RequestEntityTooLarge)
def too_large(e):
    return jsonify({"error": "File too large.", "match": False}), 413


@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Aadhaar OCR API"})


@app.route('/verify', methods=['POST'])
def verify():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            return jsonify({"error": "No JSON body received", "match": False}), 400

        image_base64   = data.get("image_base64", "")
        aadhaar_number = data.get("aadhaar_number", "").replace(" ", "").replace("-", "")

        if not image_base64:
            return jsonify({"error": "image_base64 is required", "match": False}), 400

        if not aadhaar_number:
            return jsonify({"error": "aadhaar_number is required", "match": False}), 400

        # Strip data URI prefix
        if "," in image_base64:
            image_base64 = image_base64.split(",")[1]

        # Decode base64
        try:
            image_bytes = base64.b64decode(image_base64)
        except Exception as e:
            return jsonify({"error": "Invalid base64: " + str(e), "match": False}), 400

        # Open image
        try:
            image = Image.open(BytesIO(image_bytes))
            image.load()
        except Exception as e:
            return jsonify({"error": "Cannot open image: " + str(e), "match": False}), 400

        # Free raw bytes immediately
        del image_bytes
        image_base64 = None

        # Compress aggressively
        image = compress_image(image)

        # OCR pass 1
        raw_text = pytesseract.image_to_string(image, config='--oem 3 --psm 6')

        # OCR pass 2 if nothing found
        if raw_text.strip() == "":
            raw_text = pytesseract.image_to_string(image, config='--oem 3 --psm 11')

        del image

        numbers_found = extract_aadhaar_numbers(raw_text)
        match         = aadhaar_number in numbers_found

        return jsonify({
            "match":         match,
            "numbers_found": numbers_found,
            "entered":       aadhaar_number,
            "message":       "Match found" if match else "Number not found on card"
        })

    except MemoryError:
        return jsonify({"error": "Image too large.", "match": False}), 500

    except Exception as e:
        return jsonify({"error": "Server error: " + str(e), "match": False}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
