from flask import Flask, request, jsonify
import pytesseract
import base64
import io
import re
from PIL import Image, ImageEnhance, ImageFilter
from datetime import datetime

app = Flask(__name__)

print("✅ Tesseract OCR server starting...")

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "service": "BudgetPilot OCR",
        "status": "running",
        "version": "2.1"
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
        
        # Convert to RGB
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        print(f"Image size: {image.size}")
        
        # Try multiple OCR strategies
        all_text = ""
        
        # Strategy 1: Original image
        text1 = pytesseract.image_to_string(image)
        all_text += " " + text1
        print(f"Strategy 1 (original): {text1[:200]}...")
        
        # Strategy 2: Grayscale + Contrast
        gray = image.convert('L')
        enhancer = ImageEnhance.Contrast(gray)
        high_contrast = enhancer.enhance(2.0)
        text2 = pytesseract.image_to_string(high_contrast)
        all_text += " " + text2
        print(f"Strategy 2 (contrast): {text2[:200]}...")
        
        # Strategy 3: Resize larger (helps with small text)
        width, height = image.size
        if width < 1000:
            scale = 1500 / width
            new_size = (int(width * scale), int(height * scale))
            resized = image.resize(new_size, Image.LANCZOS)
            text3 = pytesseract.image_to_string(resized)
            all_text += " " + text3
            print(f"Strategy 3 (resized): {text3[:200]}...")
        
        # Strategy 4: Sharpen
        sharpened = image.filter(ImageFilter.SHARPEN)
        text4 = pytesseract.image_to_string(sharpened)
        all_text += " " + text4
        print(f"Strategy 4 (sharpened): {text4[:200]}...")
        
        # Combine all text and parse
        print(f"\n=== COMBINED TEXT ===\n{all_text[:500]}")
        
        parsed = parse_payment_text(all_text)
        parsed['raw_text'] = all_text[:800]
        
        print(f"\n=== PARSED RESULT ===")
        print(f"Amount: {parsed['amount']}")
        print(f"Merchant: {parsed['merchant']}")
        print(f"Date: {parsed['date']}")
        print(f"Type: {parsed['type']}")
        
        return jsonify(parsed)
        
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e), "amount": None}), 500


def parse_payment_text(text):
    """Extract payment details from OCR text"""
    
    result = {
        "amount": None,
        "merchant": None,
        "date": None,
        "type": None
    }
    
    # Normalize text - keep more characters for rupee symbol variations
    t = ' '.join(text.split())
    t_lower = t.lower()
    
    print(f"\nParsing text length: {len(t)}")
    
    # ═══════════════════════════════════════════════════════
    # AMOUNT EXTRACTION - More aggressive patterns
    # ═══════════════════════════════════════════════════════
    
    # First try to find numbers with .00 pattern (very common in payment apps)
    decimal_amounts = re.findall(r'(\d{1,6}(?:,\d{2,3})*\.\d{2})', t)
    print(f"Found decimal amounts: {decimal_amounts}")
    
    for amt_str in decimal_amounts:
        try:
            amount = float(amt_str.replace(',', ''))
            if 0.01 <= amount <= 1000000:
                result['amount'] = amount
                print(f"Matched amount from decimal pattern: {amount}")
                break
        except:
            continue
    
    # If no decimal amount found, try other patterns
    if not result['amount']:
        amount_patterns = [
            r'[₹]\s*([\d,]+(?:\.\d{1,2})?)',              # ₹500.00
            r'Rs\.?\s*([\d,]+(?:\.\d{1,2})?)',             # Rs. 500
            r'INR\.?\s*([\d,]+(?:\.\d{1,2})?)',            # INR 500
            r'%\s*([\d,]+\.\d{2})',                        # % sometimes OCR'd as ₹
            r'2\s*([\d,]+\.\d{2})',                        # ₹ sometimes OCR'd as 2
            r'[EF&]\s*([\d,]+\.\d{2})',                    # Other misreads
            r'Sent\s*[^\d]*([\d,]+(?:\.\d{2})?)',          # Sent 500.00
            r'Paid\s*[^\d]*([\d,]+(?:\.\d{2})?)',          # Paid 500.00
            r'Amount[:\s]*([\d,]+(?:\.\d{1,2})?)',         # Amount: 500
        ]
        
        for pattern in amount_patterns:
            match = re.search(pattern, t, re.IGNORECASE)
            if match:
                amt_str = match.group(1).replace(',', '')
                try:
                    amount = float(amt_str)
                    if 0.01 <= amount <= 1000000:
                        result['amount'] = amount
                        print(f"Matched amount from pattern '{pattern}': {amount}")
                        break
                except:
                    continue
    
    # ═══════════════════════════════════════════════════════
    # TRANSACTION TYPE
    # ═══════════════════════════════════════════════════════
    if re.search(r'\b(sent|paid|debited|debit|payment\s+successful|transferred|spent)\b', t_lower):
        result['type'] = 'debit'
    elif re.search(r'\b(received|credited|credit|added|refund|cashback)\b', t_lower):
        result['type'] = 'credit'
    else:
        result['type'] = 'debit'  # Default
    
    print(f"Transaction type: {result['type']}")
    
    # ═══════════════════════════════════════════════════════
    # MERCHANT/RECIPIENT - Look for name patterns
    # ═══════════════════════════════════════════════════════
    
    # Pattern 1: ALL CAPS names (common in payment apps)
    caps_names = re.findall(r'\b([A-Z][A-Z]+(?:\s+[A-Z]+){1,4})\b', t)
    skip_words = {'SAMSUNG', 'WALLET', 'INDIA', 'BANK', 'AXIS', 'HDFC', 'ICICI', 
                  'PAYMENT', 'TRANSACTION', 'SUCCESSFUL', 'UPI', 'SENT', 'FROM',
                  'DATE', 'NOTES', 'THE', 'AND', 'FOR', 'MAR', 'JAN', 'FEB', 'APR',
                  'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'}
    
    for name in caps_names:
        words = name.split()
        # Skip if any word is in skip list
        if not any(w in skip_words for w in words):
            if len(name) >= 5:  # At least 5 chars
                result['merchant'] = name
                print(f"Found merchant (CAPS): {name}")
                break
    
    # Pattern 2: "To <Name>" or "Paid to <Name>"
    if not result['merchant']:
        to_patterns = [
            r'(?:To|Paid\s+to|Sent\s+to)[:\s]+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})',
        ]
        for pattern in to_patterns:
            match = re.search(pattern, t)
            if match:
                result['merchant'] = match.group(1).strip()
                print(f"Found merchant (To pattern): {result['merchant']}")
                break
    
    # ═══════════════════════════════════════════════════════
    # DATE EXTRACTION
    # ═══════════════════════════════════════════════════════
    months_map = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    # Pattern: DD MMM YYYY (17 MAR 2026)
    date_match = re.search(r'(\d{1,2})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s,]*(\d{4})', t, re.IGNORECASE)
    if date_match:
        day = int(date_match.group(1))
        month = months_map.get(date_match.group(2).lower()[:3], 1)
        year = date_match.group(3)
        if 1 <= day <= 31:
            result['date'] = f"{day:02d}/{month:02d}/{year}"
            print(f"Found date: {result['date']}")
    
    # Fallback: DD/MM/YYYY
    if not result['date']:
        date_match = re.search(r'(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})', t)
        if date_match:
            result['date'] = f"{date_match.group(1)}/{date_match.group(2)}/{date_match.group(3)}"
    
    if not result['date']:
        result['date'] = datetime.now().strftime('%d/%m/%Y')
    
    return result


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
