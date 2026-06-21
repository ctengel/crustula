"""Tools to deal with cookiex.txt files

See cli() function for CLI doc
"""

import http.cookiejar
import http.cookies
import sys
import shlex
import tempfile

def convert_header_to_cookies_txt(header_string: str, output_file: str) -> None:
    """Converts cookies header to cookies.txt

    header_string is the HTTP Cookie: header value string from client request
    output_file is the name of a new netscape cookies.txt to generate"""

    # Create a new cookie jar
    cookie_jar = http.cookiejar.MozillaCookieJar()

    # Parse the header string and populate the cookie jar
    cookies = http.cookies.SimpleCookie()
    cookies.load(header_string)
    for key, morsel in cookies.items():
        #print(key, morsel)
        # Create a Cookie object and add it to the jar
        cookie = http.cookiejar.Cookie(
            version=0,
            name=key,
            value=morsel.value,
            port=None,
            port_specified=False,
            domain='',
            domain_specified=False,
            domain_initial_dot=False,
            path='/',
            path_specified=True,
            secure=False,
            expires=None,
            discard=False,
            comment=None,
            comment_url=None,
            rest={'HttpOnly': None},
            rfc2109=False
        )
        cookie_jar.set_cookie(cookie)
    #print(cookie_jar)
    # Save the cookies to a cookies.txt file
    cookie_jar.save(output_file, ignore_discard=True)

def convert_header_to_cookies_str(header_string: str) -> str:
    """Converts cookies header to string

    header_string is the HTTP Cookie: header value string from client request"""
    with tempfile.NamedTemporaryFile(delete=True) as tmpfile:
        convert_header_to_cookies_txt(header_string, tmpfile.name)
        tmpfile.seek(0)
        return tmpfile.read().decode('utf-8')

def extract_curl_string(curl_cmd: str) -> tuple[str, str, dict[str, str]]:
    """Extracts URL, cookies header, and other headers from a curl command string

    Handles both Firefox ("Copy as cURL") and Chromium ("Copy as cURL") exports:
    - header names are matched case-insensitively (Chromium emits lowercase ``cookie:``)
    - both ``-H``/``--header`` and ``-b``/``--cookie`` can supply the cookie value
    - unknown flags (``--compressed``, ``--data-raw``, ...) are ignored

    Returns (url, cookies_header, other_headers) where other_headers maps non-cookie
    header names to their values (e.g. ``{"User-Agent": "Mozilla/5.0"}``)."""
    args = shlex.split(curl_cmd)
    url = ''
    cookies_header = ''
    other_headers: dict[str, str] = {}
    # flags that take a value we don't care about, so we skip the following arg
    skip_value_flags = {'-X', '--request', '-d', '--data', '--data-raw',
                        '--data-binary', '-e', '--referer', '-A', '--user-agent',
                        '-u', '--user', '-o', '--output'}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == 'curl':
            i += 1
            continue
        if arg in ('-H', '--header') and i + 1 < len(args):
            name, sep, value = args[i + 1].partition(':')
            name = name.strip()
            value = value.strip()
            if name.lower() == 'cookie':
                cookies_header = value
            elif name and sep:  # require an actual "name: value" header
                other_headers[name] = value
            i += 2
            continue
        if arg in ('-b', '--cookie') and i + 1 < len(args):
            # -b can be a cookie string ("a=1; b=2") or a file reference; we only
            # use it as a cookie string when it looks like name=value pairs
            candidate = args[i + 1]
            if '=' in candidate:
                cookies_header = candidate
            i += 2
            continue
        if arg in skip_value_flags and i + 1 < len(args):
            i += 2
            continue
        if arg.startswith(('http://', 'https://')):
            url = arg
        i += 1
    return (url, cookies_header, other_headers)

def cookies_txt_from_curl(curl_cmd: str) -> str | None:
    """Extracts the Cookie header from a curl command and returns cookies.txt string

    Returns None when the curl command carries no Cookie header, so callers can
    avoid overwriting stored cookies with an empty jar."""
    _, cookie_header, _ = extract_curl_string(curl_cmd)
    if not cookie_header:
        return None
    return convert_header_to_cookies_str(cookie_header)

def cli():
    """CLI to take curl and generate cookies.txt

    First arg is the cookies.txt filename
    Rest of args is a curl command
    Can use firefox "copy to CURL" feature
    """
    cookiefile = sys.argv[1]
    assert sys.argv[2] == 'curl'
    for arg in sys.argv[3:]:
        if arg.startswith('Cookie: '):
            cookieinput = arg.removeprefix('Cookie: ')
            #print(cookieinput, cookiefile)
            convert_header_to_cookies_txt(cookieinput, cookiefile)
            return
    assert False

if __name__ == '__main__':
    cli()
