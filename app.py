import os
import re
import base64
import pytesseract
from flask import Flask, request, jsonify
from PIL import Image
from io import BytesIO

app = Flask(__name__)

# ── Verhoeff tables ───────────────────────────────────────────
D = [
    [0,1,2,3,4,5,6,7,8,9],
    [1,2,3,4,0,6,7,8,9,5],
    [2,3,4,0,1,7,8,9,5,6],
    [3,4,0,1,2,8,9,5,6,7],
    [4,0,1,2,3,9,5,6,7,8],
    [5,9,8,7,6,0,4,3,2,1],
    [6,5,9,8,7,1,0,4,3,2],
    [7,6,5,9,8,2,1,0,4,3],
    [8,7,6,5,9,3,2,1,0,4],
    [9,8,7,6,5,4,3,2,1,0],
]
P = [
    [0,1,2,3,4,5,6,7,8,9],
    [1,5,7,6,2,8,3,0,9,4],
    [5,8,0,3,7,9,6,1,4,2],
    [8,9,1,6,0,4,3,5,2,7],
    [9,4,5,3,1,2,6,8,7,0],
    [4,2,8,6,5,7,3,9,0,1],
    [2,7,9,3,8,0,6,4,1,5],
    [7,0,4,6,9,1,3,2,5,8],
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
    # Grayscale halves RAM vs RGB and improves OCR
    image = image.convert('L')
    # Cap width at 1200px
    if image.width > 1200:
        ratio = 1200 / image.width
        image = image.resize((1200, int(image.height * ratio)), Image.LANCZOS)
    # Cap height at 1800px
    if image.height > 1800:
        ratio = 1800 / image.height
        image = image.resize((int(image.width * ratio), 1800), Image.LANCZOS)
    return image


@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Aadhaar OCR API"})


@app.route('/verify', methods=['POST'])
def verify():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            return jsonify({"error": "No JSON body received"}), 400

        image_base64   = data.get("image_base64", "")
        aadhaar_number = data.get("aadhaar_number", "").replace(" ", "").replace("-", "")

        if not image_base64:
            return jsonify({"error": "image_base64 is required"}), 400

        if not aadhaar_number:
            return jsonify({"error": "aadhaar_number is required"}), 400

        # Strip data URI prefix if present e.g. "data:image/png;base64,..."
        if "," in image_base64:
            image_base64 = image_base64.split(",")[1]

        # Decode base64
        try:
            image_bytes = base64.b64decode(image_base64)
            image       = Image.open(BytesIO(image_bytes))
        except Exception as e:
            return jsonify({"error": "Invalid image data: " + str(e)}), 400

        # Compress — resize + grayscale before OCR
        image = compress_image(image)

        # Run Tesseract — psm 6 for uniform text blocks
        raw_text = pytesseract.image_to_string(image, config='--oem 3 --psm 6')

        # Retry with psm 11 (sparse text) if nothing found
        if raw_text.strip() == "":
            raw_text = pytesseract.image_to_string(image, config='--oem 3 --psm 11')

        numbers_found = extract_aadhaar_numbers(raw_text)
        match         = aadhaar_number in numbers_found

        return jsonify({
            "match":         match,
            "numbers_found": numbers_found,
            "entered":       aadhaar_number,
            "message":       "Match found" if match else "Aadhaar number mismatching."
        })

    except MemoryError:
        return jsonify({"error": "Image too large. Please upload a smaller file."}), 413

    except Exception as e:
        return jsonify({"error": "Server error: " + str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
