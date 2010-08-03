from BeautifulSoup import BeautifulSoup, NavigableString
from waveapi import element
import re
import htmlentitydefs

IMAGE_PLACEHOLDER = '***{{((IMAGE_ELEMENT))}}***'

whitespace_re = re.compile('\s+')

fix_styles = {
    'background-color': 'backgroundColor',
    'font-family': 'fontFamily',
    'font-size': 'fontSize',
    'font-style': 'fontStyle',
    'font-weight': 'fontWeight',
    'text-decoration': 'textDecoration',
    'vertical-align': 'verticalAlign',
}.iteritems()

def append_content_to_blip(blip, content, type=None):
    if not content:
        return
    
    if type == 'text/plain':
        # Replace characters that Wave breaks on
        text = content.replace('\t', ' ').replace('\r', '\n')
        blip.append(text)
        return
    
    imgs = []
    
    # originally by Pamela Fox (Google)
    # http://google-wave-resources.googlecode.com/svn/trunk/samples/extensions/robots/python/maildigester/handler.py
    def cleanup(soup):
        for tag in soup:
            if isinstance(tag, NavigableString):
                # escape <>& and unescape everything else
                text = unescape(tag)
                text = text.replace('&', '&amp;')
                text = text.replace('<', '&lt;')
                text = text.replace('>', '&gt;')
                # strip whitespace
                text = whitespace_re.sub(' ', text)
                tag.replaceWith(text)
            else:
                if tag.name == 'img':
                    imgs.append({'url': tag.get('src'),
                                'width': tag.get('width'),
                                'height': tag.get('height')})
                    # replace it with an image element later.
                    # add padding.
                    tag.replaceWith(' ' + IMAGE_PLACEHOLDER)
                elif tag.name == 'a':
                    href = tag.get('href')
                    if href:
                        tag['href'] = href.replace('&amp;', '&')
                style = tag.get('style')
                if style:
                    tag['style'] = replace_all(style, fix_styles)
                cleanup(tag)
    
    soup = BeautifulSoup(content.strip())
    cleanup(soup)
    html = unicode(soup)
    # Wave doesn't like tabs
    html = html.replace('\t', ' ')
    # Since its HTML, it should use <br>s instead of line breaks.
    html = html.replace('\r', '').replace('\n', '')
    
    blip.append_markup(html)
    
    # Because append_markup doesn't accept images, we replace img tags in the
    # html with placeholders and then replace them with image elements.
    for img in imgs:
        image = element.Image(url=img['url'],
                              width=img['width'],
                              height=img['height'])
        placeholder = blip.first(IMAGE_PLACEHOLDER)
        # Image elements don't allow links on them.
        # So insert an extra space after images so that a link can still
        # be clicked if it would normally be on the image.
        placeholder.replace(image)
        placeholder.insert_after(' ')


def unescape(text):
    '''
    Replaces HTML entities with unicode characters
    
    by Fredrik Lundh
    http://effbot.org/zone/re-sub.htm#unescape-html
    '''
    def fixup(m):
        text = m.group(0)
        if text[:2] == "&#":
            # character reference
            try:
                if text[:3] == "&#x":
                    return unichr(int(text[3:-1], 16))
                else:
                    return unichr(int(text[2:-1]))
            except ValueError:
                pass
        else:
            # named entity
            try:
                text = unichr(htmlentitydefs.name2codepoint[text[1:-1]])
            except KeyError:
                pass
        return text # leave as is
    return re.sub("&#?\w+;", fixup, text)

def replace_all(text, replacements):
    for find, replace in replacements.items():
        text = text.replace(find, replace)
    return text