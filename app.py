from flask import Flask, request, jsonify
import easyocr
import base64
import io
import re
from PIL import Image
from datetime import datetime

app = Flask(__name__)

# Initialize EasyOCR (English only for speed - add 'hi' for Hindi if needed)
print("Loading OCR model...")
reader = easyocr.Reader(['en'], gpu=False)
print("OCR model loaded!")

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "service": "BudgetPilot OCR",
        "status": "running",
        "endpoints": {
            "/parse": "POST - Parse payment screenshot",
            "/health": "GET - Health check"
        }
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
        image_data = base64.b64decode(data['image'])
        image = Image.open(io.BytesIO(image_data))
        
        # Convert to RGB if necessary
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Run OCR
        results = reader.readtext(image)
        
        # Combine all detected text
        full_text = ' '.join([r[1] for r in results])
        
        print(f"OCR Text: {full_text[:200]}...")
        
        # Parse payment details
        parsed = parse_payment_text(full_text)
        parsed['raw_text'] = full_text[:300]
        
        return jsonify(parsed)
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"error": str(e), "amount": None}), 500


def parse_payment_text(text):
    """Extract payment details from OCR text using regex"""
    
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
        r'[₹]\s*([\d,]+(?:\.\d{1,2})?)',                    # ₹500.00
        r'Rs\.?\s*([\d,]+(?:\.\d{1,2})?)',                   # Rs. 500
        r'INR\.?\s*([\d,]+(?:\.\d{1,2})?)',                  # INR 500
        r'Amount[:\s]*([\d,]+(?:\.\d{1,2})?)',               # Amount: 500
        r'Total[:\s]*[₹Rs\.INR\s]*([\d,]+(?:\.\d{1,2})?)',  # Total: 500
        r'Paid[:\s]*[₹Rs\.INR\s]*([\d,]+(?:\.\d{1,2})?)',   # Paid 500
        r'Sent[:\s]*[₹Rs\.INR\s]*([\d,]+(?:\.\d{1,2})?)',   # Sent 500
        r'Received[:\s]*[₹Rs\.INR\s]*([\d,]+(?:\.\d{1,2})?)', # Received 500
        r'Debited[:\s]*[₹Rs\.INR\s]*([\d,]+(?:\.\d{1,2})?)', # Debited 500
        r'Credited[:\s]*[₹Rs\.INR\s]*([\d,]+(?:\.\d{1,2})?)', # Credited 500
        r'\b([\d,]+\.\d{2})\b'                               # 500.00 fallback
    ]
    
    for pattern in amount_patterns:
        match = re.search(pattern, t, re.IGNORECASE)
        if match:
            amount_str = match.group(1).replace(',', '')
            try:
                amount = float(amount_str)
                if 0.01 < amount < 10000000:  # Between 1 paisa and 1 crore
                    result['amount'] = amount
                    break
            except:
                continue
    
    # ═══════════════════════════════════════════════════════
    # TRANSACTION TYPE
    # ═══════════════════════════════════════════════════════
    debit_keywords = ['sent', 'paid', 'debited', 'debit', 'payment to', 
                      'transferred', 'spent', 'deducted', 'withdrawn']
    credit_keywords = ['received', 'credited', 'credit', 'payment from', 
                       'added', 'refund', 'cashback', 'deposited']
    
    for kw in debit_keywords:
        if kw in t_lower:
            result['type'] = 'debit'
            break
    
    if not result['type']:
        for kw in credit_keywords:
            if kw in t_lower:
                result['type'] = 'credit'
                break
    
    # Default to debit (most screenshots are payments)
    if not result['type']:
        result['type'] = 'debit'
    
    # ═══════════════════════════════════════════════════════
    # MERCHANT/RECIPIENT
    # ═══════════════════════════════════════════════════════
    merchant_patterns = [
        # "To Name" patterns
        r'(?:To|Paid to|Sent to|Payment to)[:\s]+([A-Za-z][A-Za-z0-9\s&\'.,\-]{1,35}?)(?:\s+on|\s+via|\s+UPI|\s*[₹]|\s*Rs|\n|$)',
        # "From Name" patterns (for credits)
        r'(?:From|Received from|Payment from)[:\s]+([A-Za-z][A-Za-z0-9\s&\'.,\-]{1,35}?)(?:\s+on|\s+via|\s+UPI|\s*[₹]|\s*Rs|\n|$)',
        # Name before ₹ symbol
        r'([A-Z][A-Za-z0-9\s]{2,20})\s*[₹]',
        # UPI ID - extract name part
        r'([a-zA-Z][a-zA-Z0-9._-]{1,25})@[a-zA-Z]+',
        # Generic patterns
        r'Merchant[:\s]+([A-Za-z][A-Za-z0-9\s&\'.,\-]{1,35})',
        r'Payee[:\s]+([A-Za-z][A-Za-z0-9\s&\'.,\-]{1,35})',
        r'Beneficiary[:\s]+([A-Za-z][A-Za-z0-9\s&\'.,\-]{1,35})'
    ]
    
    for pattern in merchant_patterns:
        match = re.search(pattern, t, re.IGNORECASE)
        if match:
            merchant = match.group(1).strip()
            # Clean up
            merchant = re.sub(r'\s+(on|via|UPI|ref|txn|transaction).*$', '', merchant, flags=re.IGNORECASE).strip()
            # Validate
            if len(merchant) > 1 and not merchant.isdigit() and merchant.lower() not in ['to', 'from', 'the', 'a', 'an']:
                result['merchant'] = merchant[:40]  # Limit length
                break
    
    # ═══════════════════════════════════════════════════════
    # DATE EXTRACTION
    # ═══════════════════════════════════════════════════════
    months_map = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    date_patterns = [
        # DD/MM/YYYY or DD-MM-YYYY
        (r'(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})', 'dmy'),
        # DD MMM YYYY
        (r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s,]+(\d{4})', 'dMy'),
        # MMM DD, YYYY
        (r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})[\s,]+(\d{4})', 'Mdy'),
        # DD/MM/YY
        (r'(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2})\b', 'dmy_short')
    ]
    
    for pattern, fmt in date_patterns:
        match = re.search(pattern, t, re.IGNORECASE)
        if match:
            try:
                if fmt == 'dmy':
                    day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
                elif fmt == 'dMy':
                    day = int(match.group(1))
                    month = months_map.get(match.group(2).lower()[:3], 1)
                    year = match.group(3)
                elif fmt == 'Mdy':
                    month = months_map.get(match.group(1).lower()[:3], 1)
                    day = int(match.group(2))
                    year = match.group(3)
                elif fmt == 'dmy_short':
                    day, month = int(match.group(1)), int(match.group(2))
                    year = 2000 + int(match.group(3))
                
                # Validate date
                if 1 <= day <= 31 and 1 <= month <= 12:
                    result['date'] = f"{day:02d}/{month:02d}/{year}"
                    break
            except:
                continue
    
    # Default to today if no date found
    if not result['date']:
        result['date'] = datetime.now().strftime('%d/%m/%Y')
    
    return result


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
