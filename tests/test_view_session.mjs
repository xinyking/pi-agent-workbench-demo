import test from 'node:test';
import assert from 'node:assert/strict';
import {createViewSession} from '../workbench/static/view-session.mjs';

test('switching identity invalidates delayed responses and closes owned subscriptions',()=>{
 const session=createViewSession(),closed=[];
 const first=session.begin('chat-a');session.own(()=>closed.push('old-stream'));
 const second=session.begin('chat-b');
 assert.equal(session.isCurrent(first),false);assert.equal(session.isCurrent(second),true);
 assert.deepEqual(closed,['old-stream']);
 const reopened=session.begin('chat-a');
 assert.equal(session.isCurrent(first),false);assert.equal(session.isCurrent(reopened),true);
});
test('completed subscriptions can release ownership; closing also cancels pending reconnects',()=>{
 const session=createViewSession(),closed=[];session.begin('detail');
 const release=session.own(()=>closed.push('finished-stream'));release();
 session.own(()=>closed.push('pending-reconnect'));
 session.close();session.close();
 assert.deepEqual(closed,['pending-reconnect']);assert.equal(session.isCurrent(null),false);
});
