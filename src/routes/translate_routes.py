"""
Translation endpoint for user-facing review translations.
Uses a simple dictionary-based approach for common phrases,
and falls back to a lightweight external API when available.
"""
from flask import Blueprint, request, jsonify
import os
import json
import requests

translate_bp = Blueprint('translate', __name__)


def translate_with_openai(text, title, target_lang):
    """Translate text using OpenAI-compatible API if available."""
    api_key = os.getenv('OPENAI_API_KEY')
    api_base = os.getenv('OPENAI_API_BASE', 'https://api.openai.com/v1')
    
    if not api_key:
        return None, None
    
    target_name = 'English' if target_lang == 'en' else 'Spanish'
    
    messages = [
        {
            "role": "system",
            "content": f"You are a professional translator. Translate the following text to {target_name}. Return ONLY the translation, nothing else. Maintain the tone and style of the original."
        },
        {
            "role": "user",
            "content": text
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
                "max_tokens": 500
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            translated_text = data['choices'][0]['message']['content'].strip()
            
            # Translate title if provided
            translated_title = ''
            if title:
                title_messages = [
                    {
                        "role": "system",
                        "content": f"Translate this short title to {target_name}. Return ONLY the translation."
                    },
                    {"role": "user", "content": title}
                ]
                title_response = requests.post(
                    f"{api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": title_messages,
                        "temperature": 0.3,
                        "max_tokens": 100
                    },
                    timeout=10
                )
                if title_response.status_code == 200:
                    title_data = title_response.json()
                    translated_title = title_data['choices'][0]['message']['content'].strip()
            
            return translated_text, translated_title
    except Exception as e:
        print(f"Translation API error: {e}")
    
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
