import concurrent.futures
import sqlite3
import time
import unittest
from unittest.mock import patch

from pixel_chat_filter import blocked_reason,clean_message,safe_name
from tests import test_pixel_canvas_admin as admin_helpers


class ChatFilterTest(unittest.TestCase):
    def test_ru_en_evasions_and_extremist_codes(self):
        cases=['хуй','xуй','хyй','xyй','xуu','пизда','пuздa','п1здa','пи.z.дa','п и з д а',
               'блять','бл9ть','blyat','blyad','бл@@ять','eбaть','ёб твою мать','ебать','нахуй',
               'нахуя','на х у й','хуёвый','fuck','f u c k','fυсk','f*ck','f@ck','sh!t','sh1t',
               's h i t','𝐟𝐮𝐜𝐤','ｆｕｃｋ','f\u200buck','f\u202euck','f̶u̶c̶k̶','f😂u❤️c😇k',
               'нацист','nazi','nаzі','нaцuст','Heil Hitler','s i e g h e i l','卐','卍',
               '14/88','1 4 8 8','1.4.8.8','1488','pizdec','suka','мудак','мyдak','гондон',
               'zalupa','пидор','nидop','ni99er','ублюдок','white power','blood and honour',
               'н а ц и с т','n a z i','n4z1','nаааazi','sieg heil','хайль','ᛋᛋ','88','8 8','gas the jews','kill all jews','нацик','фашик','nаzіsm','фашист',
               'h i t l e r','Hіtlеr','h1tl3r','Аdоlf','swastika','🇫🇺🇨🇰','asshole',
               'сука','мудила','еблан','жид','ниггер','b!tch','b1tch','блRть','fиck','жопа','ж0па','херня','fock','fxck','phuk','p١zdа','һуй','һіtlеr','motherfucker','х*й','п*зда','бл*ть','sh*t','f**k','рубляебать','корабляблять']
        for text in cases:
            with self.subTest(text=text):self.assertIsNotNone(blocked_reason(text))

    def test_normal_canvas_and_schedule_words(self):
        cases=['Привет!','Давайте нарисуем кота','ККЭПик','коридор','Поставь пиксель на 14, 88',
               'рубля','корабля','употреблять','страхуйся','жидкость','художник','дружба',
               'название','называется','класс','finish it','was hit','classmate','cocktail',
               'Scunthorpe','assistance','Общее полотно','Почему нельзя закрашивать?',
               'Пиксель в секунду','Первая пара','😂❤️🐈','Расписание на завтра','Есть 88 пикселей',
               'Белый цвет','Мама-анархия','до 7 октября','Красиво получилось','Шрифт больше',
               'Самолёт','Нарисуем знак плюс','Хочу с вами рисовать','Я буду рисовать справа',
               'Убирайте руку','Едем дальше','углублять','ослаблять','употребляй','перестрахуюсь','страхуем','Страх уйдёт','Здравствуйте','Я из группы 101-Д9','Her name is Anna','Земля','Fact','Пишите сюда','Напишите сообщение','Распишитесь','Сшить костюм']
        for text in cases:
            with self.subTest(text=text):self.assertIsNone(blocked_reason(text))
        self.assertIsNotNone(blocked_reason('корабля и хуй'))
        self.assertIsNotNone(blocked_reason('cocktail fuck'))

    def test_bounds_controls_names_and_links(self):
        self.assertEqual(clean_message('  Привет\n всем\u202e!  '),'Привет всем!')
        self.assertIsNone(clean_message('a'*201));self.assertIsNone(clean_message(['text']))
        self.assertIsNone(clean_message('\u202e\u200b'));self.assertIsNone(clean_message('\u200d'));self.assertIsNone(clean_message('\u0301'))
        self.assertEqual(safe_name('Нацист'), 'Участник')
        self.assertEqual(safe_name('Иван'),'Иван')
        for text in ['https://example.com','www.example.ru','t.me/bot','example.org']:
            self.assertEqual(blocked_reason(text),'links')


class ChatApiTest(unittest.TestCase):
    setUp=admin_helpers.CanvasAdminTest.setUp
    tearDown=admin_helpers.CanvasAdminTest.tearDown
    headers=admin_helpers.CanvasAdminTest.headers
    def send(self,text='Рисуем кота',rid='chat-request-001',headers=None):
        return self.client.post('/chat/messages',json={'text':text,'request_id':rid},headers=headers or self.student)
    def read(self,headers=None,after=-1):
        return self.client.get('/chat/messages?after='+str(after),headers=headers or self.student)
    def state(self,after=-1):
        return self.client.get('/state?x0=-64&y0=-128&x1=64&y1=128&chat_after='+str(after),headers=self.owner)

    def test_verified_author_public_privacy_and_safe_text(self):
        r=self.client.post('/chat/messages',json={'text':'<svg onload=alert(1)>','request_id':'chat-safe-text','actor_id':1084693264,'name':'Админ'},headers=self.student)
        self.assertEqual(r.status_code,200)
        message=r.get_json()['message'];self.assertTrue(message['own']);self.assertFalse(message['admin'])
        self.assertNotIn('actor_id',message)
        owner=self.read(self.owner).get_json()['messages'][0]
        self.assertEqual(owner['actor_id'],42);self.assertFalse(owner['own'])
        self.assertEqual(owner['text'],'<svg onload=alert(1)>')
        self.assertNotEqual(owner['name'],'Админ')
        self.assertEqual(self.read().headers['Cache-Control'],'private, no-store')
        private_in_url=self.client.get('/chat/messages',query_string={'tgWebAppData':self.owner['X-Telegram-Init-Data']})
        self.assertEqual(private_in_url.status_code,200)
        self.assertFalse(private_in_url.get_json()['admin'])
        self.assertNotIn('actor_id',private_in_url.get_json()['messages'][0])

    def test_notifications_only_new_visible_other_author_messages(self):
        self.assertEqual(self.state().get_json()['chat']['unread'],0)
        self.assertEqual(self.send().status_code,200)
        meta=self.state(0).get_json()['chat'];self.assertEqual(meta['unread'],1)
        self.assertEqual(self.state(meta['head']).get_json()['chat']['unread'],0)
        self.assertEqual(self.read(after=0).get_json()['chat']['unread'],0)
        self.client.post('/admin/chat/hide',json={'message_id':meta['head']},headers=self.owner)
        hidden=self.state(0).get_json()['chat'];self.assertEqual(hidden['unread'],0)
        self.assertGreater(hidden['revision'],meta['revision']);self.assertEqual(hidden['head'],meta['head'])
        self.assertEqual(self.read().get_json()['messages'],[])

    def test_idempotent_send_payload_conflict_and_hidden_retry(self):
        first=self.send().get_json();self.assertEqual(self.send().get_json()['message']['id'],first['message']['id'])
        self.assertEqual(self.send(text='Другое сообщение').status_code,409)
        self.client.post('/admin/chat/hide',json={'message_id':first['message']['id']},headers=self.owner)
        retry=self.send();self.assertEqual(retry.status_code,200);self.assertIsNone(retry.get_json()['message'])
        self.assertEqual(self.read().get_json()['messages'],[])

    def test_persistent_cooldown_exact_boundary_and_burst(self):
        now=time.time_ns()//1_000_000
        with patch('pixel_chat.time.time_ns',return_value=now*1_000_000):self.assertEqual(self.send().status_code,200)
        with patch('pixel_chat.time.time_ns',return_value=(now+4999)*1_000_000):
            r=self.send(rid='chat-request-002');self.assertEqual(r.status_code,429);self.assertEqual(r.get_json()['chat']['remaining_ms'],1)
        with patch('pixel_chat.time.time_ns',return_value=(now+5000)*1_000_000):self.assertEqual(self.send(rid='chat-request-002').status_code,200)
        for n in range(2,6):
            with patch('pixel_chat.time.time_ns',return_value=(now+n*5000)*1_000_000):self.assertEqual(self.send(rid=f'chat-number-{n:03}').status_code,200)
        with patch('pixel_chat.time.time_ns',return_value=(now+30_000)*1_000_000):self.assertEqual(self.send(rid='chat-request-seven').status_code,429)
        with patch('pixel_chat.time.time_ns',return_value=(now+60_000)*1_000_000):self.assertEqual(self.send(rid='chat-request-seven').status_code,200)

    def test_filter_server_side_and_rejected_attempt_limit(self):
        r=self.send('пuздa');self.assertEqual(r.status_code,400);self.assertEqual(self.read().get_json()['messages'],[])
        self.assertEqual(self.send('Исправил',rid='corrected-request').status_code,429)
        with sqlite3.connect(self.path) as db:db.execute('UPDATE canvas_chat_senders SET attempted_at_ms=0')
        self.assertEqual(self.send('Исправил',rid='corrected-request').status_code,200)

    def test_separate_mute_and_unmute_atomic_audit(self):
        self.send()
        payload={'telegram_id':42,'banned':True}
        self.assertEqual(self.client.post('/admin/chat/ban',json=payload,headers=self.owner).status_code,200)
        self.assertEqual(self.send(rid='blocked-send').status_code,403)
        self.assertTrue(self.read().get_json()['chat']['banned'])
        self.assertFalse(self.client.get('/state?x0=0&y0=0&x1=2&y1=2',headers=self.student).get_json()['banned'])
        self.assertEqual(len(self.read().get_json()['messages']),1)
        self.client.post('/admin/chat/ban',json=payload,headers=self.owner)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM canvas_chat_moderation').fetchone()[0],1)
        self.assertEqual(self.client.get('/admin/chat/bans',headers=self.web).get_json()['bans'][0]['telegram_id'],42)
        self.assertEqual(self.client.post('/admin/chat/ban',json={'telegram_id':42,'banned':False},headers=self.web).status_code,200)
        self.assertFalse(self.read().get_json()['chat']['banned'])
        self.assertEqual(self.client.post('/admin/chat/ban',json={'telegram_id':1084693264,'banned':True},headers=self.owner).status_code,400)

    def test_auth_origin_invalid_queries_and_body(self):
        self.assertEqual(self.client.get('/chat/messages').status_code,401)
        self.assertEqual(self.client.post('/chat/messages',json={'text':'Привет','request_id':'valid-request'}).status_code,401)
        self.assertEqual(self.send(headers={**self.student,'Origin':'https://foreign.example'}).status_code,403)
        self.assertEqual(self.client.post('/chat/messages',query_string={'tgWebAppData':self.student['X-Telegram-Init-Data']},json={'text':'Привет','request_id':'valid-request'}).status_code,401)
        for endpoint,payload in [('hide',{'message_id':1}),('ban',{'telegram_id':42,'banned':True})]:
            self.assertEqual(self.client.post('/admin/chat/'+endpoint,json=payload,headers=self.student).status_code,403)
            self.assertEqual(self.client.post('/admin/chat/'+endpoint,json=payload,headers={**self.owner,'Origin':'https://foreign.example'}).status_code,403)
        for after in ['-2','9007199254740992','bad']:
            self.assertEqual(self.client.get('/chat/messages?after='+after,headers=self.student).status_code,400)
            self.assertEqual(self.client.get('/state?x0=0&y0=0&x1=2&y1=2&chat_after='+after,headers=self.student).status_code,400)
        self.assertEqual(self.send('a'*201).status_code,400)
        self.assertEqual(self.send(rid='<invalid>').status_code,400)

    def test_concurrent_tabs_one_send_and_duplicate_receipt(self):
        def place(n):
            with self.app.test_client() as client:
                return client.post('/chat/messages',json={'text':'Кот','request_id':f'concurrent-chat-{n}'},headers=self.student).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:codes=list(pool.map(place,range(8)))
        self.assertEqual(codes.count(200),1);self.assertEqual(codes.count(429),7)
        with sqlite3.connect(self.path) as db:db.execute('UPDATE canvas_chat_senders SET attempted_at_ms=0')
        def repeat(n):
            with self.app.test_client() as client:return client.post('/chat/messages',json={'text':'Кот','request_id':'same-chat-request'},headers=self.student).get_json()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(repeat,range(8)))
        self.assertTrue(all(r['success'] for r in results));self.assertEqual(len({r['message']['id'] for r in results}),1)

    def test_snapshot_latest_50_event_readonly_and_restart(self):
        with sqlite3.connect(self.path) as db:
            for n in range(120):db.execute("INSERT INTO canvas_chat_messages(actor_id,actor_name,text,at_ms,request_id) VALUES(42,'Иван','Кот',?,?)",(n,'fixture-'+str(n)))
            db.execute('UPDATE canvas_event SET started_at_ms=1,ends_at_ms=2')
        data=self.read(self.owner,after=0).get_json();self.assertEqual(len(data['messages']),50)
        self.assertEqual([m['id'] for m in data['messages']],list(range(71,121)))
        self.assertEqual(data['chat']['unread'],100)
        self.assertEqual(self.send().status_code,423)
        from pixel_canvas import initialize
        initialize(self.path)
        self.assertEqual(len(self.read().get_json()['messages']),50)
        self.assertEqual(self.state(0).get_json()['revision'],0)

if __name__=='__main__':unittest.main()
