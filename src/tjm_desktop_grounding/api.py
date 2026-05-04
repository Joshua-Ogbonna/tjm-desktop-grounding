from __future__ import annotations

from tjm_desktop_grounding.models import BlogPost

POSTS_URL = "https://jsonplaceholder.typicode.com/posts"

FALLBACK_POSTS = [
    BlogPost(
        id=index,
        title=f"Fallback post {index}",
        body=(
            "The JSONPlaceholder API was unavailable, so this local fallback "
            "keeps the automation workflow testable."
        ),
    )
    for index in range(1, 11)
]


def fetch_posts(limit: int = 10, timeout_seconds: float = 10.0) -> list[BlogPost]:
    try:
        import requests
    except ImportError:
        return FALLBACK_POSTS[:limit]

    try:
        response = requests.get(POSTS_URL, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return FALLBACK_POSTS[:limit]

    posts: list[BlogPost] = []
    for item in payload[:limit]:
        try:
            posts.append(
                BlogPost(
                    id=int(item["id"]),
                    title=str(item["title"]),
                    body=str(item["body"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return posts or FALLBACK_POSTS[:limit]
