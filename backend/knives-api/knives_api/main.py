from typing import List
from fastapi import FastAPI

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from .model import Knives
DATABASE_URL = "mysql+mysqlconnector://root:@localhost:3306/knives"

app = FastAPI()
engine = create_engine(DATABASE_URL)

@app.get("/")
def read_root():
    return {"Message" : "HELLO"}

@app.get("/knives")
def read_knives() -> List[Knives]:
    session = Session(engine)
    stmt = select(Knives)
    resp: List[Knives] = []
    for knife in session.scalars(stmt):
        resp.append(knife)
    return resp