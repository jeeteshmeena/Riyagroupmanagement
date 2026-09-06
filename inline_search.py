from telegram import InlineQueryResultArticle, InputTextMessageContent
from rapidfuzz import fuzz
from database import load_db


RESULTS_PER_PAGE = 10


def search_inline(query, offset):

    db = load_db()

    offset = int(offset) if offset else 0

    results = []

    matches = []

    for name, data in db.items():

        score = fuzz.ratio(query.lower(), name)

        if score > 60:

            matches.append(data)

    matches.sort(key=lambda x: x["text"])

    sliced = matches[offset: offset + RESULTS_PER_PAGE]

    for story in sliced:
        banner = story.get("banner_url") or story.get("banner") or story.get("photo") or story.get("image") or story.get("poster_url")
        thumb = banner if (banner and str(banner).startswith(("http://", "https://"))) else None
        results.append(
            InlineQueryResultArticle(
                id=str(story.get("name") or story.get("text", "")),
                title=story.get("text", story.get("name", "Story")),
                description=f"{story.get('platform', 'Story')} • {story.get('genre', '')}",
                thumbnail_url=thumb,
                input_message_content=InputTextMessageContent(
                    f"{story.get('text', '')}\n{story.get('link', '')}"
                )
            )
        )

    next_offset = offset + RESULTS_PER_PAGE

    if next_offset >= len(matches):

        next_offset = ""

    return results, str(next_offset)
