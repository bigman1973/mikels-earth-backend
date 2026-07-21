"""
Translation endpoint for user-facing review translations.
Uses OpenAI API (gpt-4o-mini) for high-quality translations.
"""
from flask import Blueprint, request, jsonify
import os
import requests

translate_bp = Blueprint('translate', __name__)


def translate_with_openai(text, title, target_lang):
    """Translate text using OpenAI API."""
    api_key = os.getenv('OPENAI_API_KEY')
    api_base = os.getenv('OPENAI_API_BASE', 'https://api.openai.com/v1')
    
    if not api_key:
        print("[TRANSLATE] ERROR: OPENAI_API_KEY not found in environment")
        return None, None
    
    print(f"[TRANSLATE] Using API base: {api_base}")
    print(f"[TRANSLATE] API key starts with: {api_key[:10]}...")
    
    target_name = 'English' if target_lang == 'en' else 'Spanish'
    
    # Translate text and title together in one call for efficiency
    prompt_text = text
    if title:
        prompt_text = f"Title: {title}\n\nText: {text}"
    
    messages = [
        {
            "role": "system",
            "content": f"You are a professional translator. Translate the following to {target_name}. If there is a 'Title:' and 'Text:' section, translate both and return them in the format:\nTitle: [translated title]\nText: [translated text]\n\nIf there is no title section, just return the translation directly. Maintain the tone and style of the original."
        },
        {
            "role": "user",
            "content": prompt_text
        }
    ]
    
    try:
        response = requests.post(
            f"{api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 600
            },
            timeout=15
        )
        
        print(f"[TRANSLATE] OpenAI response status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            result = data['choices'][0]['message']['content'].strip()
            
            # Parse title and text from response
            translated_title = ''
            translated_text = result
            
            if title and 'Title:' in result and 'Text:' in result:
                parts = result.split('Text:', 1)
                title_part = parts[0].replace('Title:', '').strip()
                text_part = parts[1].strip() if len(parts) > 1 else result
                translated_title = title_part
                translated_text = text_part
            elif title and '\n' in result:
                # Fallback: first line is title, rest is text
                lines = result.split('\n', 1)
                translated_title = lines[0].strip()
                translated_text = lines[1].strip() if len(lines) > 1 else result
            
            return translated_text, translated_title
        else:
            print(f"[TRANSLATE] OpenAI error response: {response.text}")
            return None, None
            
    except Exception as e:
        print(f"[TRANSLATE] Exception: {type(e).__name__}: {e}")
    
    return None, None


@translate_bp.route('', methods=['POST'])
def translate_text():
    """
    POST /api/translate
    Body: { "text": "...", "title": "...", "target": "en"|"es" }
    Returns: { "translated_text": "...", "translated_title": "..." }
    """
    data = request.get_json()
    
    if not data or 'text' not in data:
        return jsonify({'error': 'Missing text field'}), 400
    
    text = data['text']
    title = data.get('title', '')
    target = data.get('target', 'en')
    
    if target not in ('en', 'es'):
        return jsonify({'error': 'Target must be "en" or "es"'}), 400
    
    if not text.strip():
        return jsonify({'translated_text': '', 'translated_title': ''}), 200
    
    # Try OpenAI translation
    translated_text, translated_title = translate_with_openai(text, title, target)
    
    if translated_text:
        return jsonify({
            'translated_text': translated_text,
            'translated_title': translated_title or ''
        }), 200
    
    # Fallback: return original text with a note
    return jsonify({
        'translated_text': text,
        'translated_title': title,
        'note': 'Translation service unavailable'
    }), 200
