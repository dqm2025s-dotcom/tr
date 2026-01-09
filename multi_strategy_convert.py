import concurrent.futures
import json
import re
import time
import os
import sys

# [Installation]
# Please install the required libraries before running this script:
# pip install pdfplumber pymupdf pypdf

try:
    import pdfplumber
    import fitz  # PyMuPDF
    from pypdf import PdfReader
except ImportError as e:
    print(f"Error: {e}")
    print("Please install required libraries: pip install pdfplumber pymupdf pypdf")
    sys.exit(1)

# --- Parsing Logic ---

def clean_text(text):
    """Cleans up text by removing extra spaces and normalizing newlines."""
    if not text:
        return ""
    # Remove the table rows like "사랑 돈 사업..." and "상 상 상..."
    # Also handle the clumped pypdf version "사랑돈사업..."

    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        if "사랑 돈 사업" in line or "승진 취업 매매" in line:
            continue
        if "사랑돈사업" in line:
            continue

        # Rudimentary check for the value row (e.g. "상 상 하 ...")
        # Matches lines with only Hangul chars (상/중/하) and spaces, length > 5 to avoid false positives
        if re.match(r'^\s*[상중하](\s+[상중하])+\s*$', line):
            continue
        # Check for clumped "상상상상..."
        if re.match(r'^[상중하]{5,}$', line.strip()):
            continue

        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    return text.strip()

def preprocess_pypdf(text):
    """Inserts newlines to make pypdf text more parseable."""
    # 1. Header: Digit. Digit/Word (Name)
    # Be careful not to break "3.5" or "Section 3.1".
    # Pattern seen: "1. 0 바보", "5. 5 동전", "1. 6 동전", "3. 컵"
    # We look for "Digit. " followed by specific start chars or digits.
    # We add \n before it.

    # Major Arcana: 1. 0, 2. 1, ...
    text = re.sub(r'(?<!\n)(\d+\.\s+(?:\d+|Ace|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Page|Knight|Queen|King|컵|지팡이|동전|검)\s+)', r'\n\1', text)

    # Sections
    text = re.sub(r'(?<!\n)(1\)\s*키워드)', r'\n\1', text)
    text = re.sub(r'(?<!\n)(2\)\s*회화적)', r'\n\1', text)
    text = re.sub(r'(?<!\n)(3\)\s*실전)', r'\n\1', text)
    text = re.sub(r'(?<!\n)(4\)\s*상담)', r'\n\1', text)

    return text

def parse_card_block(block):
    """Parses a single card block text into a dictionary."""
    lines = block.strip().split('\n')
    if not lines:
        return None

    # Extract Card Name (first line)
    raw_header = lines[0].strip()

    # Remove the leading section number
    card_name = re.sub(r'^\d+\.\s+', '', raw_header)

    # Clean card name further if it has trailing content due to bad splitting
    # e.g. "0 바보 (The Fool) 1) 키워드" -> "0 바보 (The Fool)"
    if "1) 키워드" in card_name:
        card_name = card_name.split("1) 키워드")[0].strip()

    # regex for sections
    keywords_match = re.search(r'1\)\s*키워드(.*?)(?:2\)\s*회화적|3\)\s*실전|4\)\s*상담|$)', block, re.DOTALL)
    keywords_text = keywords_match.group(1) if keywords_match else ""

    # Parse keywords
    keywords_text = clean_text(keywords_text)
    keywords_list = []
    if keywords_text:
        # Split by comma or newline
        raw_keywords = re.split(r'[,.\n]', keywords_text)
        for k in raw_keywords:
            k = k.strip()
            if k.startswith('-'): k = k[1:].strip()
            if k:
                keywords_list.append(k)

    # Meaning Up: Combine "회화적 설명" and "실전 상담"
    desc_match = re.search(r'2\)\s*회화적\s*설명(.*?)(?:3\)\s*실전|4\)\s*상담|$)', block, re.DOTALL)
    desc_text = clean_text(desc_match.group(1)) if desc_match else ""

    practical_match = re.search(r'3\)\s*실전\s*상담(.*?)(?:4\)\s*상담|$)', block, re.DOTALL)
    practical_text = clean_text(practical_match.group(1)) if practical_match else ""

    meaning_up = ""
    if desc_text:
        meaning_up += f"[회화적 설명]\n{desc_text}\n\n"
    if practical_text:
        meaning_up += f"[실전 상담]\n{practical_text}"
    meaning_up = meaning_up.strip()

    # Meaning Down: "상담 TIP"
    tip_match = re.search(r'4\)\s*상담\s*TIP(.*?)(?:$)', block, re.DOTALL)
    meaning_down = clean_text(tip_match.group(1)) if tip_match else ""

    if not meaning_up and not meaning_down and not keywords_list:
        return None

    return {
        "card_name": card_name,
        "meaning_up": meaning_up,
        "meaning_down": meaning_down,
        "keywords": keywords_list
    }

def parse_full_text(text):
    """Splits full text into card blocks and parses them."""
    # Ensure text starts with newline
    text = "\n" + text

    # Split by header pattern
    # The header pattern: \n + Digits + . + space + content
    parts = re.split(r'\n(\s*\d+\.\s+[^\n]+)', text)

    blocks = []

    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        content = parts[i+1] if i+1 < len(parts) else ""
        full_block = header + "\n" + content

        # Filter: Is this really a card block?
        if "1) 키워드" in full_block:
            blocks.append(full_block)

    results = []
    for block in blocks:
        card_data = parse_card_block(block)
        if card_data:
            results.append(card_data)

    return results

# --- Strategies ---

def strategy_pdfplumber(pdf_path):
    print("Running strategy: pdfplumber")
    full_text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
    except Exception as e:
        print(f"Error in pdfplumber strategy: {e}")
        return []

    return parse_full_text(full_text)

def strategy_pymupdf(pdf_path):
    print("Running strategy: PyMuPDF (fitz)")
    full_text = ""
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            text = page.get_text()
            if text:
                full_text += text + "\n"
    except Exception as e:
        print(f"Error in PyMuPDF strategy: {e}")
        return []

    return parse_full_text(full_text)

def strategy_pypdf(pdf_path):
    print("Running strategy: pypdf")
    full_text = ""
    try:
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            text = page.extract_text()
            if text:
                # Preprocess specifically for pypdf
                text = preprocess_pypdf(text)
                full_text += text + "\n"
    except Exception as e:
        print(f"Error in pypdf strategy: {e}")
        return []

    return parse_full_text(full_text)

# --- Main Execution ---

def run_strategy(name, func, pdf_path):
    start_time = time.time()
    try:
        data = func(pdf_path)
    except Exception as e:
        print(f"Strategy {name} failed with exception: {e}")
        data = []
    end_time = time.time()
    duration = end_time - start_time
    return name, data, duration

def main():
    # User requested file: data/tr_td.pdf
    # Actual file in repo (likely typo): data/tr_dt.pdf
    # Logic: try requested first, fallback to existing.

    target_file = "data/tr_td.pdf"
    fallback_file = "data/tr_dt.pdf"

    if os.path.exists(target_file):
        pdf_path = target_file
    elif os.path.exists(fallback_file):
        print(f"Warning: '{target_file}' not found. Using '{fallback_file}' instead.")
        pdf_path = fallback_file
    else:
        print(f"Error: Neither '{target_file}' nor '{fallback_file}' found.")
        return

    strategies = [
        ("pdfplumber", strategy_pdfplumber),
        ("fitz", strategy_pymupdf),
        ("pypdf", strategy_pypdf)
    ]

    results = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_to_strategy = {
            executor.submit(run_strategy, name, func, pdf_path): name
            for name, func in strategies
        }

        for future in concurrent.futures.as_completed(future_to_strategy):
            name = future_to_strategy[future]
            try:
                name, data, duration = future.result()
                results[name] = {"data": data, "duration": duration}
            except Exception as e:
                print(f"Strategy {name} generated an exception: {e}")
                results[name] = {"data": [], "duration": 0}

    # Save and Compare
    print("\n" + "="*40)
    print(" COMPARISON REPORT ")
    print("="*40)
    print(f"{'Strategy':<15} | {'Count':<6} | {'Size (KB)':<10} | {'Time (s)':<8}")
    print("-" * 45)

    for name in ["pdfplumber", "fitz", "pypdf"]:
        info = results.get(name, {"data": [], "duration": 0})
        data = info["data"]
        duration = info["duration"]

        output_filename = f"output_{name}.json"
        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        file_size = os.path.getsize(output_filename) / 1024

        print(f"{name:<15} | {len(data):<6} | {file_size:<10.2f} | {duration:<8.4f}")

if __name__ == "__main__":
    main()
