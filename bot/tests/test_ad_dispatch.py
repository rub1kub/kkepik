from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMediaGroup

import ad_dispatch as dispatch
from admin_post_capture import AdminPostCapture, OWNER, post_digest, write_private_json
from scripts import send_ad_20261029 as sender


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)/'admin_campaigns'
        write_private_json(self.base/'active.json',{'id':'ad-20261103'})
        self.capture=AdminPostCapture.active(self.base)
        self.post={'version':1,'messages':[{'message_id':1,'date':1793700000,
            'chat':{'id':OWNER,'type':'private'},'text':'Рекламный пост','entities':[{'type':'bold','offset':0,'length':9}]}]}
        self.campaign=dict(id='ad-20261103',owner=OWNER,phase='ready',roles=list(dispatch.ROLES),
            trigger={'type':'schedule_complete','schedule_date':'05.11.2026','broadcast_day':'2026-11-03'},
            preview_message_ids=[30],post_sha256=post_digest(self.post))
        write_private_json(self.capture.campaign_path,self.campaign)
        write_private_json(self.capture.directory/'post.json',self.post)
        self.now=datetime(2026,11,3,13,tzinfo=timezone.utc)

    def receipt(self,**changes):
        write_private_json(self.capture.directory/'schedule-complete.json',dict(campaign='ad-20261103',
            schedule_date='05.11.2026',schedule_type='groups',is_update=False,notified_count=1736,
            completed_at='2026-11-03T12:00:00+00:00',**changes))

    def gate(self,now=None):
        return dispatch.pending_campaign(self.temp.name,now or self.now)

    async def test_no_sender_is_created_while_waiting_for_main_schedule(self):
        self.assertEqual(self.gate()[1],'waiting_schedule')
        with patch.object(dispatch,'pending_campaign',return_value=(None,'waiting_schedule')),patch.object(dispatch,'Bot') as bot:
            await dispatch.run(send=False)
        bot.assert_not_called()

    def test_matching_completed_schedule_and_preview_are_both_required(self):
        self.receipt()
        self.assertEqual(self.gate()[1],'ready')
        self.campaign['phase']='awaiting_post';write_private_json(self.capture.campaign_path,self.campaign)
        self.assertEqual(self.gate()[1],'waiting_post')

    def test_later_day_and_mismatched_payload_cannot_dispatch(self):
        self.receipt()
        self.assertEqual(self.gate(datetime(2026,11,4,13,tzinfo=timezone.utc))[1],'expired')
        self.post['messages'][0]['text']='Changed after preview'
        write_private_json(self.capture.directory/'post.json',self.post)
        with self.assertRaises(ValueError):self.gate()

    def test_updates_and_wrong_receipt_dates_are_rejected(self):
        self.receipt()
        path=self.capture.directory/'schedule-complete.json';data=json.loads(path.read_text());data['is_update']=True
        write_private_json(path,data)
        with self.assertRaises(ValueError):self.gate()
        data['is_update']=False;data['schedule_date']='06.11.2026';write_private_json(path,data)
        with self.assertRaises(ValueError):self.gate()

    async def test_success_and_restart_skip_already_delivered_users(self):
        targets=[dict(user_id=11,role='Я студент'),dict(user_id=22,role='Я преподаватель')]
        async def delivery(bot,uid,post,record,retry_at=0):
            record(dict(user_id=uid,status='attempting'))
            return dict(user_id=uid,status='sent',message_ids=[uid+100])
        bot=AsyncMock();send=AsyncMock(side_effect=delivery)
        with patch.object(dispatch,'current_recipients',return_value=targets),patch.object(dispatch,'deliver',send),patch.object(dispatch,'record_message'):
            report=await dispatch.send_campaign(self.capture,self.campaign,self.post,bot)
            self.assertEqual(report['counts'],{'sent':2})
            self.assertEqual([c.args[1] for c in send.await_args_list],[11,22])
            self.assertEqual(send.await_args_list[0].args[2],self.post)
            again=await dispatch.send_campaign(self.capture,self.campaign,self.post,bot)
        self.assertTrue(again['complete'])
        self.assertEqual(send.await_count,2)
        bot.send_message.assert_awaited_once()
        self.assertEqual(json.loads(self.capture.campaign_path.read_text())['phase'],'complete')

    async def test_uncertain_attempt_is_retained_without_resending(self):
        targets=[dict(user_id=11,role='Я студент'),dict(user_id=22,role='Я преподаватель')]
        write_private_json(self.capture.directory/'recipients.json',targets)
        (self.capture.directory/'results.jsonl').write_text(json.dumps(dict(user_id=11,status='attempting'))+'\n')
        deliver=AsyncMock(return_value=dict(user_id=22,status='sent',message_ids=[122]))
        with patch.object(dispatch,'deliver',deliver),patch.object(dispatch,'record_message'):
            report=await dispatch.send_campaign(self.capture,self.campaign,self.post,AsyncMock())
        self.assertEqual(report['counts'],{'unknown_outcome':1,'sent':1})
        self.assertEqual([c.args[1] for c in deliver.await_args_list],[22])

    async def test_blocked_album_recipient_does_not_stop_the_campaign(self):
        error = TelegramBadRequest(
            method=SendMediaGroup(chat_id=22, media=[]),
            message='Bad Request: failed to send message #1 with the error message "USER_IS_BLOCKED"',
        )
        with patch.object(sender, 'eligible', return_value=True), patch.object(sender, 'send_saved_post', side_effect=error):
            result = await sender.deliver(AsyncMock(), 22, self.post, lambda row: None)
        self.assertEqual(result, {'user_id':22, 'status':'unreachable'})

    async def test_resume_retries_definitive_rejection_without_touching_sent_or_uncertain(self):
        self.receipt()
        self.campaign['phase'] = 'paused_error'
        write_private_json(self.capture.campaign_path, self.campaign)
        self.assertEqual(self.gate()[1], 'paused_error')
        self.assertEqual(dispatch.pending_campaign(self.temp.name, self.now, resume=True)[1], 'ready')
        targets = [{'user_id': uid, 'role':'Я студент'} for uid in (11, 22, 33, 44)]
        write_private_json(self.capture.directory/'recipients.json', targets)
        rows = [{'user_id': uid, 'status': status} for uid, status in
                [(11, 'sent'), (22, 'payload_error'), (33, 'unknown_outcome')]]
        journal = self.capture.directory/'results.jsonl'
        journal.write_text(''.join(json.dumps(row)+'\n' for row in rows))

        async def delivery(bot, uid, post, record, retry_at=0):
            return {'user_id':uid, 'status':'unreachable'}

        send = AsyncMock(side_effect=delivery)
        with patch.object(dispatch, 'deliver', send):
            report = await dispatch.send_campaign(self.capture, self.campaign, self.post, AsyncMock(), resume=True)
        self.assertEqual([call.args[1] for call in send.await_args_list], [22, 44])
        self.assertEqual(report['counts'], {'sent':1, 'unknown_outcome':1, 'unreachable':2})
        self.assertTrue(report['complete'])
