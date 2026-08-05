from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager
from fastapi import fastapi


@asynccontextmanager
async def lifespan(app):
    scheduler = AsyncIOScheduler()
    scheduler.start()
    with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    app.state.db.close()
    container.stop()

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="./frontend/src/"), name="static")
templates = Jinja2Templates(directory="./frontend/templates")

@app.get("/test")
def test(db):
    return {"status": "ok"}
