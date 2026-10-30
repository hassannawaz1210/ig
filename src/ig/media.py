"""Fetch Instagram video files, including redirects between its media servers."""
from urllib.parse import urljoin, urlsplit


def fetch_video(session, url):
    for _ in range(5):
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not any(
                host == domain or host.endswith('.' + domain)
                for domain in ('cdninstagram.com', 'fbcdn.net')):
            raise ValueError('Unexpected video host')
        response = session.get(url, stream=True, timeout=30, allow_redirects=False)
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get('Location')
        response.close()
        if not location:
            raise ValueError('Missing video redirect location')
        url = urljoin(url, location)
    raise ValueError('Too many video redirects')
