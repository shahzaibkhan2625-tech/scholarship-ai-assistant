from fastapi import FastAPI

app = FastAPI(title="Scholarship AI Assistant")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
