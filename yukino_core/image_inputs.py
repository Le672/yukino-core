"""Explicit image command inputs; quoted messages stay in their own session."""
from urllib.parse import urlsplit
from .emoji_collector import image_segments,media_url,CollectionError
from .public_net import canonical_url,NetworkError

def image_command_event(event,plugins):
    """Use actual text segments for image commands, preserving the stored event.

    QQ joins attachments to text without inserting whitespace. Strip only the
    placeholders introduced by real image segments, never literal user text,
    and only when the remaining text invokes the installed image workshop.
    """
    if not image_segments(event.get('segments',[]),limit=1):return event
    text=''.join(str(segment.get('data',{}).get('text',''))
                 for segment in event.get('segments',[]) if isinstance(segment,dict)
                 and segment.get('type')=='text' and isinstance(segment.get('data'),dict)).strip()
    item,_=plugins.match(text)
    return {**event,'text':text} if item and item['id']=='d_images' else event

async def command_images(store,event):
    images=image_segments(event.get('segments',[]),limit=3)
    if not images and event.get('reply_to'):
        quoted=await store.message_event(event['session_id'],event['reply_to'])
        if quoted is not None:images=image_segments(quoted.get('segments',[]),limit=3)
    urls=[]
    for image in images:
        data=image['data'];raw=data.get('url') or data.get('file')
        if not isinstance(raw,str) or len(raw)>4096:continue
        try:
            host=(urlsplit(raw).hostname or '').lower()
            if host=='qlogo.cn' or host.endswith('.qlogo.cn'):continue
            try:url=media_url(raw)
            except CollectionError:url=canonical_url(raw)
        except (ValueError,NetworkError):continue
        if url not in urls:urls.append(url)
    return urls
