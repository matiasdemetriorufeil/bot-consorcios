from fastapi import FastAPI

app = FastAPI(title="Bot Consorcios - Estudio Diego Rufeil")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
