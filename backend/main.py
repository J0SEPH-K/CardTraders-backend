from fastapi import FastAPI
app = FastAPI(title="CardTraders API")
@app.get("/health")
def health(): return {"status":"ok"}
