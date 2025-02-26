import re
from lxml import etree
import itertools


def ai_html2plaintext(html, body_id=None, encoding='utf-8'):
    """
    Modified version of html2plaintext specifically for AI-generated content
    that's already been processed with markdown.markdown()

    This version avoids adding extra formatting characters that cause
    issues with numbered lists and headings.
    """
    if not (html and html.strip()):
        return ''

    if isinstance(html, bytes):
        html = html.decode(encoding)
    else:
        assert isinstance(html, str), f"expected str got {html.__class__.__name__}"

    # Pre-process to fix list numbering issues
    html = re.sub(r'<li>\s*(\d+)\.\s*<strong>(.*?)</strong>', r'<li>\1. \2', html)

    tree = etree.fromstring(html, parser=etree.HTMLParser())

    if body_id is not None:
        source = tree.xpath('//*[@id=%s]' % (body_id,))
    else:
        source = tree.xpath('//body')
    if len(source):
        tree = source[0]

    # Convert links and images to plaintext without references
    for link in tree.findall('.//a'):
        link.tag = 'span'

    for img in tree.findall('.//img'):
        img.tag = 'span'
        img.text = 'Image'

    html = etree.tostring(tree, encoding="unicode")
    # \r char is converted into &#13;, must remove it
    html = html.replace('&#13;', '')

    # Do NOT add markdown-style formatting characters that cause issues
    html = html.replace('<strong>', '').replace('</strong>', '')
    html = html.replace('<b>', '').replace('</b>', '')
    html = html.replace('<h3>', '').replace('</h3>', '\n')
    html = html.replace('<h2>', '').replace('</h2>', '\n')
    html = html.replace('<h1>', '').replace('</h1>', '\n')
    html = html.replace('<em>', '').replace('</em>', '')
    html = html.replace('<tr>', '\n')
    html = html.replace('</p>', '\n')
    html = re.sub(r'<br\s*/?>', '\n', html)
    html = re.sub('<.*?>', ' ', html)
    html = html.replace(' ' * 2, ' ')
    html = html.replace('&gt;', '>')
    html = html.replace('&lt;', '<')
    html = html.replace('&amp;', '&')
    html = html.replace('&nbsp;', '\N{NO-BREAK SPACE}')

    # strip all lines
    html = '\n'.join([x.strip() for x in html.splitlines()])
    html = html.replace('\n' * 2, '\n')

    # Fix any remaining list numbering issues
    html = re.sub(r'\*(\d+)\.\s*', r'\1. ', html)

    return html.strip()