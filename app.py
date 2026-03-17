from flask import Flask, request, jsonify
import pytesseract
import base64
import io
import re
from PIL import Image
from datetime import datetime

app = Flask(__name__)

print("✅ Tesseract OCR server starting...")

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "service": "BudgetPilot OCR",
        "status": "running",
        "version": "2.0-lite"
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/parse', methods=['POST'])
def parse_screenshot():
    try:
        data = request.json
        
        if not data or 'image' not in data:
            return jsonify({"error": "No image provided", "amount": None}), 400
        
        # Decode base64 image
        try:
            image_data = base64.b64decode(data['image'])
            image = Image.open(io.BytesIO(image_data))
        except Exception as e:
            return jsonify({"error": f"Invalid image: {str(e)}", "amount": None}), 400
        
        # Convert to RGB if necessary
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Run Tesseract OCR
        full_text = pytesseract.image_to_string(image)
        
        print(f"OCR Text: {full_text[:300]}...")
        
        # Parse payment details
        parsed = parse_payment_text(full_text)
        parsed['raw_text'] = full_text[:500]
        
        print(f"Parsed: amount={parsed['amount']}, merchant={parsed['merchant']}")
        
        return jsonify(parsed)
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"error": str(e), "amount": None}), 500


def parse_payment_text(text):
    """Extract payment details from OCR text"""
    
    result = {
        "amount": None,
        "merchant": None,
        "date": None,
        "type": None
    }
    
    # Normalize text
    t = ' '.join(text.split())
    t_lower = t.lower()
    
    # ═══════════════════════════════════════════════════════
    # AMOUNT EXTRACTION
    # ═══════════════════════════════════════════════════════
    amount_patterns = [
        r'[₹]\s*([\d,]+(?:\.\d{1,2})?)',              # ₹500.00
        r'Rs\.?\s*([\d,]+(?:\.\d{1,2})?)',             # Rs. 500
        r'INR\.?\s*([\d,]+(?:\.\d{1,2})?)',            # INR 500
        r'[zZ]\s*([\d,]+\.\d{2})',                     # Z10.00 (₹ misread)
        r'Amount[:\s]*([\d,]+(?:\.\d{1,2})?)',
        r'Total[:\s]*([\d,]+(?:\.\d{1,2})?)',
        r'Paid[:\s]*([\d,]+(?:\.\d{1,2})?)',
        r'Sent[:\s]*([\d,]+(?:\.\d{1,2})?)',
        r'\b(\d{1,3}(?:,\d{2,3})*\.\d{2})\b',         # 1,00,000.00
        r'\b(\d+\.\d{2})\b',                           # 10.00
    ]
    
    for pattern in amount_patterns:
        matches = re.findall(pattern, t, re.IGNORECASE)
        for match in matches:
            amount_str = match.replace(',', '')
            try:
                amount = float(amount_str)
                if 0.01 <= amount <= 10000000:
                    result['amount'] = amount
                    break
            except:
                continue
        if result['amount']:
            break
    
    # ═══════════════════════════════════════════════════════
    # TRANSACTION TYPE
    # ═══════════════════════════════════════════════════════
    debit_keywords = ['sent', 'paid', 'debited', 'debit', 'payment successful',
                      'transferred', 'spent', 'money sent']
    credit_keywords = ['received', 'credited', 'credit', 'added', 'refund', 
                       'cashback', 'money received']
    
    for kw in debit_keywords:
        if kw in t_lower:
            result['type'] = 'debit'
            break
    
    if not result['type']:
        for kw in credit_keywords:
            if kw in t_lower:
                result['type'] = 'credit'
                break
    
    if not result['type']:
        result['type'] = 'debit'
    
    # ═══════════════════════════════════════════════════════
    # MERCHANT/RECIPIENT
    # ═══════════════════════════════════════════════════════
    merchant_patterns = [
        r'(?:To|Paid to|Sent to)[:\s]+([A-Za-z][A-Za-z0-9\s]{2,30})',
        r'(?:From|Received from)[:\s]+([A-Za-z][A-Za-z0-9\s]{2,30})',
        r'([A-Z][A-Z\s]{4,25}[A-Z])\s*[₹zZ]',
    ]
    
    for pattern in merchant_patterns:
        match = re.search(pattern, t, re.IGNORECASE)
        if match:
            merchant = match.group(1).strip()
            merchant = re.sub(r'\s+(on|via|UPI|ref|txn).*$', '', merchant, flags=re.IGNORECASE).strip()
            if len(merchant) > 2 and not merchant.isdigit():
                result['merchant'] = merchant[:40]
                break
    
    # Look for ALL CAPS name
    if not result['merchant']:
        caps_matches = re.findall(r'\b([A-Z][A-Z\s]{4,25}[A-Z])\b', t)
        skip_words = ['SAMSUNG', 'WALLET', 'INDIA', 'BANK', 'AXIS', 'HDFC', 
                     'ICICI', 'PAYMENT', 'TRANSACTION', 'SUCCESSFUL', 'UPI']
        for m in caps_matches:
            if not any(sw in m for sw in skip_words):
                result['merchant'] = m.strip()
                break
    
    # ═══════════════════════════════════════════════════════
    # DATE EXTRACTION
    # ═══════════════════════════════════════════════════════
    months_map = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    date_patterns = [
        (r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s,]+(\d{4})', 'dMy'),
        (r'(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})', 'dmy'),
        (r'(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2})\b', 'dmy_short'),
    ]
    
    for pattern, fmt in date_patterns:
        match = re.search(pattern, t, re.IGNORECASE)
        if match:
            try:
                if fmt == 'dMy':
                    day = int(match.group(1))
                    month = months_map.get(match.group(2).lower()[:3], 1)
                    year = match.group(3)
                elif fmt == 'dmy':
                    day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
                elif fmt == 'dmy_short':
                    day, month = int(match.group(1)), int(match.group(2))
                    year = 2000 + int(match.group(3))
                
                if 1 <= day <= 31 and 1 <= month <= 12:
                    result['date'] = f"{day:02d}/{int(month):02d}/{year}"
                    break
            except:
                continue
    
    if not result['date']:
        result['date'] = datetime.now().strftime('%d/%m/%Y')
    
    return result


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
