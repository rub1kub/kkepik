import sys, asyncio, json, uuid
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from webmax import WebMaxClient
from webmax.database.db import Database
from webmax import payloads

async def diag():
    client = WebMaxClient(session_name='_diag_temp', phone='+79180241117')
    await client.connect_web_socket()
    print('1. WebSocket OK')

    client.db = Database(db_path='_diag_temp.db')
    await client.db.init()

    instance = payloads.UserAgent(os_version='Linux', device_name='Chrome')
    client.user_agent = instance.to_dict()
    receiver = asyncio.create_task(client.message_receiver())

    client.device_id = str(uuid.uuid4())
    resp = await client.init(device_id=client.device_id)
    print(f'2. Init response: {json.dumps(resp, ensure_ascii=False)}')

    receiver.cancel()
    import os
    os.remove('_diag_temp.db')

asyncio.run(diag())
