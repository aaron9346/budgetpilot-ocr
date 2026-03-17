from flask import Flask, request, jsonify
import easyocr
import base64
import io
import re
from PIL import Image
from datetime import datetime

app = Flask(__name__)

# Initialize EasyOCR
print("Loading OCR model... (this takes ~30 seconds on first run)")
reader = easyocr.Reader(['en'], gpu=False)
print("✅ OCR model loaded!")

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "service": "BudgetPilot OCR",
        "status": "running",
        "version": "1.1"
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
        
        print(f"Image size: {image.size}")
        
        # Run OCR
        results = reader.readtext(image)
        
        # Combine all detected text
        texts = [r[1] for r in results]
        full_text = ' '.join(texts)
        
        print(f"OCR found {len(texts)} text blocks")
        print(f"Full text: {full_text[:300]}...")
        
        # Parse payment details
        parsed = parse_payment_text(full_text, texts)
        parsed['raw_text'] = full_text[:500]
        parsed['text_blocks'] = texts[:20]  # First 20 blocks for debugging
        
        print(f"Parsed result: amount={parsed['amount']}, merchant={parsed['merchant']}")
        
        return jsonify(parsed)
        
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "amount": None}), 500


def parse_payment_text(full_text, text_blocks):
    """Extract payment details from OCR text"""
    
    result = {
        "amount": None,
        "merchant": None,
        "date": None,
        "type": None
    }
    
    # Normalize
    t = ' '.join(full_text.split())
    t_lower = t.lower()
    
    # ═══════════════════════════════════════════════════════
    # AMOUNT EXTRACTION - Multiple strategies
    # ═══════════════════════════════════════════════════════
    
    # Strategy 1: Look for ₹ symbol (might be OCR'd as various characters)
    amount_patterns = [
        # Standard rupee patterns
        r'[₹]\s*([\d,]+(?:\.\d{1,2})?)',              # ₹500.00
        r'Rs\.?\s*([\d,]+(?:\.\d{1,2})?)',             # Rs. 500
        r'INR\.?\s*([\d,]+(?:\.\d{1,2})?)',            # INR 500
        
        # OCR might read ₹ as other characters
        r'[zZ]\s*([\d,]+\.\d{2})',                     # Z10.00 (₹ misread)
        r'[tTfF]\s*([\d,]+\.\d{2})',                   # Sometimes ₹ becomes t or f
        r'[\*\#]\s*([\d,]+\.\d{2})',                   # *10.00
        
        # Amount with keywords
        r'Amount[:\s]*([\d,]+(?:\.\d{1,2})?)',
        r'Total[:\s]*([\d,]+(?:\.\d{1,2})?)',
        r'Paid[:\s]*([\d,]+(?:\.\d{1,2})?)',
        r'Sent[:\s]*([\d,]+(?:\.\d{1,2})?)',
        r'Received[:\s]*([\d,]+(?:\.\d{1,2})?)',
        
        # Standalone decimal number (common in payment screenshots)
        r'\b(\d{1,3}(?:,\d{2,3})*(?:\.\d{2}))\b',      # 1,00,000.00 or 10.00
        r'\b(\d+\.\d{2})\b',                           # Simple: 10.00
    ]
    
    for pattern in amount_patterns:
        matches = re.findall(pattern, t, re.IGNORECASE)
        for match in matches:
            amount_str = match.replace(',', '')
            try:
                amount = float(amount_str)
                # Sanity check: reasonable payment amount
                if 0.01 <= amount <= 10000000:
                    result['amount'] = amount
                    break
            except:
                continue
        if result['amount']:
            break
    
    # Strategy 2: If no amount found, look in individual text blocks
    if not result['amount']:
        for block in text_blocks:
            # Look for number with decimal
            match = re.search(r'(\d+\.\d{2})', block)
            if match:
                try:
                    amount = float(match.group(1))
                    if 0.01 <= amount <= 10000000:
                        result['amount'] = amount
                        break
                except:
                    continue
    
    # ═══════════════════════════════════════════════════════
    # TRANSACTION TYPE
    # ═══════════════════════════════════════════════════════
    debit_keywords = ['sent', 'paid', 'debited', 'debit', 'payment successful',
                      'transferred', 'spent', 'money sent', 'transaction successful']
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
        result['type'] = 'debit'  # Default
    
    # ═══════════════════════════════════════════════════════
    # MERCHANT/RECIPIENT
    # ═══════════════════════════════════════════════════════
    
    # Look for name patterns
    merchant_patterns = [
        r'(?:To|Paid to|Sent to)[:\s]+([A-Z][A-Za-z\s]{2,30})',
        r'(?:From|Received from)[:\s]+([A-Z][A-Za-z\s]{2,30})',
        r'([A-Z][A-Z\s]{5,30})\s*[₹zZRs]',  # UPPERCASE NAME before amount
    ]
    
    for pattern in merchant_patterns:
        match = re.search(pattern, t)
        if match:
            merchant = match.group(1).strip()
            # Clean up
            merchant = re.sub(r'\s+', ' ', merchant).strip()
            if len(merchant) > 2 and not merchant.isdigit():
                result['merchant'] = merchant[:40]
                break
    
    # Strategy 2: Look for ALL CAPS name (common in payment apps)
    if not result['merchant']:
        caps_pattern = r'\b([A-Z][A-Z\s]{4,25}[A-Z])\b'
        matches = re.findall(caps_pattern, t)
        for m in matches:
            # Skip common headers
            skip_words = ['SAMSUNG', 'WALLET', 'INDIA', 'BANK', 'AXIS', 'HDFC', 
                         'ICICI', 'PAYMENT', 'TRANSACTION', 'SUCCESSFUL', 'UPI']
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
        # DD MMM YYYY (17 MAR 2026)
        (r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s,]+(\d{4})', 'dMy'),
        # DD/MM/YYYY
        (r'(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})', 'dmy'),
        # DD-MM-YY
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
