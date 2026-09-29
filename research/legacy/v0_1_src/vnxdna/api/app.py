from fastapi import FastAPI
from .routes import router
app = FastAPI(title='VNX-DNA R&D-1', version='1.0.0', description='Local-only computational DNA storage research API.')
app.include_router(router)
@app.get('/')
def dashboard():
    return {'application':'VNX-DNA R&D-1','status':'READY','dashboard':'Use /docs on the loopback-bound local service.'}
