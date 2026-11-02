"""Validate public Instagram post links without following arbitrary URLs."""
import re
from urllib.parse import urlsplit

CODE = re.compile(r'[A-Za-z0-9_-]{5,30}')


def post_code(value: str) -> str:
    value = value.strip()
    if CODE.fullmatch(value):
        return value
    url = urlsplit(value)
    if url.scheme in {'http', 'https'} and url.netloc.lower() in {'instagram.com', 'www.instagram.com', 'm.instagram.com'}:
        match = re.fullmatch(r'/(?:reel|reels|p)/([A-Za-z0-9_-]{5,30})/?', url.path)
        if match:
            return match.group(1)
    raise ValueError('Enter an Instagram reel/post link or shortcode')
