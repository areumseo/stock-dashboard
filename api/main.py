from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import stocks, search

app = FastAPI(title="Stockpulse Select API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router, prefix="/stocks", tags=["stocks"])
app.include_router(search.router, prefix="/search", tags=["search"])


@app.get("/health")
def health():
    return {"status": "ok"}
