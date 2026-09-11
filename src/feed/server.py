from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response

from src.config import FEED_OUTPUT, FEED_HOST, FEED_PORT
from src.feed.generator import generate_feed

app = FastAPI(title="freelancermap XML Feed")


@app.get("/feed.xml")
def serve_feed() -> Response:
    xml = generate_feed()
    return Response(content=xml, media_type="application/xml")


@app.post("/feed/regenerate")
def regenerate_feed() -> dict:
    generate_feed()
    return {"status": "ok", "path": str(FEED_OUTPUT)}


def main() -> None:
    import uvicorn

    generate_feed()
    uvicorn.run(app, host=FEED_HOST, port=FEED_PORT)


if __name__ == "__main__":
    main()
