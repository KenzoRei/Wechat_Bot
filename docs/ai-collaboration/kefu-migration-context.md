# Context: migrating customer-facing interaction from Smart Robot to WeChat Kefu

Status: **shared research/context document, not a plan yet.** Written by
Claude Code (round 54) to hand Codex everything gathered in chat with the
user before joint planning starts. This is the input to the plan, not the
plan itself — see `## Open questions for Codex` at the end for where the
real work begins.

## 1. The problem (confirmed, not hypothetical)

The platform's entire interactive bot runs on WeChat Work's **智能机器人
(Smart Robot)** — `core/webhook_receiver.py`, `core/WXBizJsonMsgCrypt.py`,
`api/webhook.py`. The user discovered, testing against a real deployment,
that this only works in **internal-employee-only** WeCom chats. Verified
independently against the official docs and multiple corroborating sources
(not taken on the user's report alone):

- WeChat Work's own backend rejects senders who aren't in the company
  address book *before* any application code sees the message. No official
  API bypasses this.
- Group chats containing external contacts (real customers) **cannot have
  any robot added at all** — this applies to Smart Robot and also to
  **消息推送/群机器人 (Group Robot Webhook, path/91770)**, the mechanism
  `jobs/uchoice_daily.py` and `handlers/uchoice/complete_request.py`
  already use for outbound pushes. 消息推送 is also structurally
  outbound-only (cannot receive/respond to a customer message), so it was
  never going to carry the interactive request flow regardless of the
  internal/external question.

**Practical consequence:** as far as we can tell, the live FedEx flow and
the U-Choice pipeline built so far have only ever been validated in an
internal test chat. Whether either has ever actually worked against a real
external customer group is now in doubt — this needs to be treated as an
open verification item, not assumed either way.

## 2. The candidate replacement: 微信客服 (WeChat Kefu)

A separate, distinct WeCom product, confirmed as the one officially
supported channel for reaching external WeChat users via API. Findings
below are from direct fetches of the official docs plus corroborating
searches — every claim below has a source, listed in section 4.

### 2.1 How it's structured (materially different from Smart Robot)

- **No group concept at all.** A Kefu conversation is one external customer
  (`external_userid`) talking to one `open_kfid` (kf/service account).
  There is no shared thread where a customer and multiple staff all see the
  same messages — nothing like the current `group_config`/`group_member`
  model.
- **Onboarding is a public link/QR code**, not "add this bot to your
  group." `POST /cgi-bin/kf/add_contact_way` with an `open_kfid` (+
  optional `scene` tracking string) returns a URL
  (`https://work.weixin.qq.com/kf/...`) any regular personal WeChat user
  can click — no WeCom account needed on their side. A business can have
  multiple kf accounts/links (e.g. per warehouse).
- **Once API mode is enabled for a kf account, it is API-only** — confirmed
  from the docs: *"开启API后，仅可通过API来管理客服帐号、分配客服会话和
  收发客服消息"* ("once API is enabled, [that account] can only be managed
  via API — account management, session assignment, and sending/receiving
  messages all go through API"). **No human 接待人员 (reception staff) is
  required** — this was checked specifically because the user's stated
  architecture (below) assumes zero human involvement on the Kefu side.

### 2.2 Message reception — two-step, not payload-in-callback

Unlike the current Smart Robot webhook (full encrypted message arrives
directly in the POST body), Kefu's callback is a thin notification:

```xml
<xml>
   <ToUserName><![CDATA[ww12345678910]]></ToUserName>
   <CreateTime>1348831860</CreateTime>
   <MsgType><![CDATA[event]]></MsgType>
   <Event><![CDATA[kf_msg_or_event]]></Event>
   <Token><![CDATA[ENCApHxnGDNAVNY4AaSJKj4Tb5mwsEMzxhFmHVGcra996NR]]></Token>
   <OpenKfId><![CDATA[wkxxxxxxx]]></OpenKfId>
</xml>
```

You then call `POST /cgi-bin/kf/sync_msg` (with a `cursor` for pagination,
the `token` from the callback, `open_kfid`) to actually pull message
content:

```json
{
    "msg_list": [
        {
            "msgid": "from_msgid_4622416642169452483",
            "open_kfid": "wkAJ2GCAAASSm4_FhToWMFea0xAFfd3Q",
            "external_userid": "wmAJ2GCAAAme1XQRC-NI-q0_ZM9ukoAw",
            "send_time": 1615478585,
            "origin": 3,
            "servicer_userid": "Zhangsan",
            "msgtype": "MSG_TYPE"
        }
    ]
}
```

`external_userid` is the stable per-customer identifier — functionally
analogous to the `wechat_openid` the whole system already keys off of.
Received message types: text, image/voice/video/file (via `media_id`),
location, link, business_card, miniprogram, msgmenu, merged_msg, and
event types (`enter_session`, `msg_send_fail`,
`session_status_change`, `kf_account_auth_change`, etc.).

### 2.3 Sending messages — the real structural constraint

`POST /cgi-bin/kf/send_msg?access_token=...`, body includes `touser`
(external_userid), `open_kfid`, `msgtype`, optional `msgid` for
deduplication. Supported outbound types: text, image, voice, video, file,
link, miniprogram, menu, location. **No markdown or template-card type** —
every markdown-formatted reply in `core/confirmation.py` and elsewhere
would need reformatting to plain text (or another supported type). Pure
rework, not a blocker.

**The real constraint, confirmed and quoted from the official doc
(path/94700):** *"仅当微信客户在主动发送消息给客服后的48小时内，企业可
发送消息给客户"* — the enterprise may only send messages within 48 hours
of the customer's last message, capped at 5 enterprise messages per window,
reset only by another customer message. **True proactive/cold-start
messaging is not possible at all** — the customer must always speak first.
Corroborated independently: [微信开放社区 thread on the same
limit](https://developers.weixin.qq.com/community/develop/doc/cd67289d208741c80d743bef0b6f29ae).

### 2.4 Callback crypto — directly reusable

Confirmed against the generic WeChat Work callback-config doc
(path/91116): Token + EncodingAESKey, GET verification handshake
(`msg_signature`/`timestamp`/`nonce`/`echostr`, decrypt-and-echo), POST
body with an `Encrypt` field, AES-based. **This is the same scheme
`core/WXBizJsonMsgCrypt.py` already implements** for Smart Robot — the
class is a generic Tencent-provided crypto helper instantiated per-app with
its own Token/EncodingAESKey/CorpID, not hardcoded to Smart Robot
specifically. It should be directly reusable for a new Kefu callback route,
with **its own separate Token/EncodingAESKey pair** (never reuse Smart
Robot's — different app, different registration in the WeCom console).

## 3. Architecture decisions already made with the user (in chat, not yet written as a signed plan)

These are settled, not open for re-litigation unless Codex finds a
technical problem with them:

1. **Staff never touch Kefu.** All staff-only operations (`adjust_storage`,
   `move_storage`, `recount_storage`, `confirm_inbound_completion`,
   `confirm_outbound_completion`, `role_change`, `register_member`) stay
   exactly where they are today: the existing internal-only Smart Robot
   group. Confirmed viable — internal-only groups are unaffected by any of
   the restrictions in section 1, since none of those restrictions are
   about internal chats.
2. **Kefu is used only for the customer-initiated slice**: creating
   `uchoice_outbound_request`/`uchoice_inbound_request`, and delivering the
   outbound instruction PDF at request-confirmation time (which happens in
   the same turn as the customer's own message — not a proactive push, no
   conflict with the 48h window).
3. **No human reception staff on the Kefu side** — the bot (our backend, via
   API) is the sole handler of every kf conversation. Confirmed compatible
   with the API-mode behavior in section 2.1.
4. **This narrows the actual migration scope significantly** — most
   existing U-Choice services need zero changes. Only the two
   customer-initiated request services, plus whatever new plumbing connects
   Kefu's callback/sync/send API to the existing `session_manager`/
   `workflow_engine` conversational engine, are in scope.

## 4. Confirmed real gaps this migration does NOT automatically close

Independently verified by reading the actual current code (not assumed):

- **`handlers/uchoice/complete_request.py`** (`CompleteExistingRequestHandler`):
  pushes a completion notice into the *original customer's group* the
  moment a warehouseman confirms completion — triggered by staff action,
  with no relationship to whether the customer has spoken recently. Under
  Kefu's rules this can only be delivered if the customer happens to be
  within an open 48h window; otherwise it cannot be sent at all until they
  message again.
- **`jobs/uchoice_daily.py`** (`_run_digest_and_retirement`) and
  **`jobs/uchoice_invoice.py`**: scheduled/cron-triggered pushes, same
  problem, worse — entirely disconnected from customer activity by design.
- **Important framing, not a new regression**: these three already cannot
  reach a real external customer group *today*, because `send_group_webhook_message`
  goes through the Group Robot Webhook mechanism, which — per section 1 —
  can't even be added to an external-contact group in the first place.
  Migrating to Kefu does not make this worse, but it also does not
  automatically fix it. **This is a genuinely open product/technical
  question, not yet resolved, and should be an explicit section of whatever
  plan Codex and Claude Code produce** — options include: accept
  "customer must return and ask" as the only delivery path, find a
  supplementary channel (e.g. WeChat Official Account template messages —
  a different, not-yet-researched product), or something else.

## 5. Current deployment/config context

- Production domain (confirmed via `config.py:48`'s `SERVER_BASE_URL`
  default, used across `api/admin/invoices.py`, `core/result_message.py`,
  `handlers/uchoice/pdf_stub.py`, `handlers/uchoice/queries.py`):
  `https://wechat-bot-atse.onrender.com`. **Not yet confirmed by the user
  as still the live/correct URL** — flag as a check item, don't assume.
- The user has started adding Kefu credential placeholders to `.env` under
  a `# Wechat Kefu` section (`url`, `token`, `EncodingAESKey`) — **names
  only, values never read into this document or any collaboration file**,
  per this project's existing "no credentials in `docs/ai-collaboration/`"
  rule. The field names don't match this project's existing convention
  (`WECHAT_TOKEN`, `WECHAT_ENCODING_AES_KEY`, all-caps with a `WECHAT_`
  prefix, loaded via `config.py`'s `_require()`) — worth standardizing
  during implementation, not a design blocker. Exact purpose of the `url`
  field (is it the callback URL the user will register, or something else)
  not yet confirmed with the user.
- `config.py` already holds `WECHAT_CORP_ID` (enterprise-wide, reusable for
  Kefu — CorpID isn't per-app) but `WECHAT_SECRET` is specifically the
  自建应用 app's secret, not reusable — Kefu needs its **own** Secret,
  obtained from the WeCom admin console once 微信客服's API mode is
  enabled (a step only the user can do, requires admin access, not yet
  done as of this writing).

## 6. API documentation reference (every doc consulted so far)

| Doc | URL | Fetched? | Covers |
|---|---|---|---|
| Bot ecosystem overview | [path/94638](https://developer.work.weixin.qq.com/document/path/94638) | Yes | Lists Smart Robot / Group Robot / Kefu as three separate products |
| Kefu overview & guide | [path/95652](https://developer.work.weixin.qq.com/document/path/95652) | Yes | What Kefu is, account/session concepts, API-only-mode statement |
| Kefu integration guide | [path/99866](https://developer.work.weixin.qq.com/document/path/99866) | Yes | Entry point linking to the specific API pages below |
| Receive messages/events | [path/94699](https://developer.work.weixin.qq.com/document/path/94699) | Yes | Callback shape, `sync_msg`, message JSON structure, message types |
| Send message | [path/94700](https://developer.work.weixin.qq.com/document/path/94700) | Yes | `send_msg` endpoint, outbound types, **48h/5-message window** |
| Message Push (消息推送) | [path/91770](https://developer.work.weixin.qq.com/document/path/91770) | Yes | Confirmed outbound-only + blocked from external-contact groups |
| Generic callback config | [path/91116](https://developer.work.weixin.qq.com/document/path/91116) | Yes | Token/EncodingAESKey, GET handshake, POST encryption — same scheme as Smart Robot |
| Get kf contact-way link | [path/94692](https://developer.work.weixin.qq.com/document/path/94692) | Yes | `add_contact_way` API — customer onboarding link/QR |
| Add reception staff | [path/94695](https://developer.work.weixin.qq.com/document/path/94695) | Yes | Confirmed NOT required once API mode is on |
| Add kf account | [path/94688](https://developer.work.weixin.qq.com/document/path/94688) | **No — referenced only** | How many kf accounts, account creation shape |
| Session assignment | [path/94698](https://developer.work.weixin.qq.com/document/path/94698) | **No — referenced only** | 分配客服会话 mechanics — relevant since we're bot-only, may be a no-op for us |
| Get customer basic info | [path/95149](https://developer.work.weixin.qq.com/document/path/95149) | **No — referenced only** | What data is available given an `external_userid` |
| Welcome-message event response | [path/95122](https://developer.work.weixin.qq.com/document/path/95122) | **No — referenced only** | Auto-greeting on `enter_session` |
| Callback notification event shapes | [path/97302](https://developer.work.weixin.qq.com/document/path/97302) | Partial | Saw the `kf_account_auth_change` event; other event shapes not yet reviewed |
| Smart Robot persistent connection | [path/101463](https://developer.work.weixin.qq.com/document/path/101463) | **No — surfaced in search only** | A newer Smart Robot mode — worth checking whether it changes the internal-only restriction at all before fully committing to the Kefu path |
| API-mode robot doc | [path/101468](https://developer.work.weixin.qq.com/document/path/101468) | **No — surfaced in search only** | Same as above, unclear relevance |

## 7. Existing code Codex should know about (reuse inventory)

- `core/WXBizJsonMsgCrypt.py` — generic per-app crypto helper, reusable for
  a new Kefu callback route (see 2.4).
- `core/webhook_receiver.py` — pattern for the GET verification handshake
  + POST decrypt; needs a parallel implementation for Kefu's
  notify-then-`sync_msg` model rather than payload-in-callback.
- `clients/wechat_client.py` (`send_message`, `send_group_webhook_message`)
  — existing send patterns; would need a new function for
  `POST /cgi-bin/kf/send_msg` (access_token auth, `touser`/`open_kfid`
  shape, respecting the 48h/5-message constraint).
- `models/group.py` (`GroupConfig`, `GroupMember`), `core/access_control.py`
  (`check_access`, keyed on `(wechat_openid, wechat_group_id)`) — this
  entire identity/authorization model assumes a WeCom group. Kefu's
  `(external_userid, open_kfid)` identity doesn't map onto it directly —
  needs new modeling, not a fit into the existing tables as-is.
- `core/session_manager.py`, `core/workflow_engine.py` — the conversational
  engine itself (session state, field collection, confirmation flow) is
  channel-agnostic in principle; the question is what identity/session key
  a Kefu-sourced turn uses to reach it.

## 8. Major pivot (post-round-56): Kefu is staff-facing, not customer-facing

The user changed the goal after round 56. Rationale, given directly by the
user and not up for re-litigation: Smart Robot only lives inside the WeCom
app, but both customers *and staff* already live in ordinary consumer
WeChat — requiring staff to run a second app just to use this tool is real
adoption friction the user wants to avoid. Kefu is reachable from ordinary
WeChat with no separate app, which is why it was chosen over "just use
Smart Robot with staff as the internal audience" (which is otherwise a
valid, much cheaper option the user was shown and explicitly declined for
this reason).

**The reformulated architecture:**

- **Staff** (not customers) are the ones who reach the bot via Kefu, using
  their own personal WeChat, exactly the way an external customer would
  have under the original plan.
- Staff initiate a service request **on behalf of** a customer — the bot
  processes/validates/records it exactly as today (same
  `workflow_engine`/`session_manager`/database/PDF pipeline), then gives
  staff a proper response.
- **Staff manually relays that response to the actual customer** through
  whatever channel they already use — the bot never talks to the customer
  directly at all under this model. This is the resolution to the
  Kefu-can't-reach-external-contacts problem: it doesn't reach them,
  staff does, manually.
- This also resolves the "notification gap" (round 55 item 3, and Claude
  Code's incorrect round-56/57 suggestion that staff-side Kefu push would
  solve it — it does not, see below): daily digests and monthly invoices
  become **pull, not push** for MVP — staff asks for them in the Kefu
  conversation, the bot responds. A later phase may additionally push a
  summary into an existing **internal-only** WeCom group (zero technical
  risk, already-proven mechanism) purely for visibility; not required for
  MVP correctness.

**Decisions the user has made:**

1. **Staff-identity mapping is required and agreed.** A Kefu-side
   `external_userid` must be resolved to a known staff member + role before
   the bot trusts them as staff. Proposed: reuse the exact
   self-registration pattern already signed/shipped in Phase 4 (register
   into a zero-grant pending state, admin assigns real role) rather than
   design a new mechanism.
2. **Role-based service scoping still applies, explicitly confirmed by the
   user** ("I don't want an accountant to create an inbound request then
   confirm it by mistake") — the existing deny-by-default grant model
   (the same mechanism already gating `role_change`, etc. today) must gate
   which services a given Kefu-mapped staff identity can invoke. Not a new
   design, an explicit requirement to carry the existing pattern over.
3. **Customer-reference resolution — open decision, not yet made.** Two
   options: (a) a separate kefu account per customer, so the backend never
   has to ask/guess which customer a request is for; (b) one (or a few,
   e.g. per-warehouse) kefu account(s), with staff explicitly stating which
   customer each request is for, resolved via the same candidate-list/
   fuzzy-match-and-disambiguate pattern already used for addresses/SKUs
   today. Claude Code's recommendation to the user: (b), on scaling and
   reuse-of-existing-pattern grounds, *unless* the real customer count is
   small enough (rough threshold discussed: under ~10) that (a) stays
   administratively simple. **User has not yet confirmed customer count or
   picked a or b — still open, needs the user's answer before this is
   settled.**
4. **48h/5-message Kefu window is less operationally severe than originally
   assessed**, now that the counterparty is staff (using this as an active
   work tool during business hours) rather than a customer who might go
   quiet for days. Does not eliminate the constraint, softens its practical
   impact.
5. **The XML-vs-JSON crypto-envelope gap Codex found in round 55 still
   applies unchanged** — irrelevant to the staff/customer pivot, still
   needs the small XML-parsing adapter described there. Confirmed to the
   user as bounded, low-risk work, not a redesign of the crypto primitives
   themselves.

**Explicitly NOT yet decided, carried forward as open:**

- Item 3 above (per-customer kefu vs. shared kefu + explicit customer
  field) — pending the user's answer on real customer count.
- Whether "customer" needs to resolve to an actual existing customer
  record in the system (matching today's WeCom-group-based customers) or
  can be a freer-form field — raised by Claude Code, not yet answered by
  the user.
- Per the user's explicit instruction (verbatim): **"I want you to discuss
  with codex for more gaps first. After i confirm all of them, you two
  then discuss about further plan."** This document's job right now is to
  hand Codex the reformulated architecture and ask it to find gaps in
  *this* shape — not to move to drafting `kefu-migration-plan.md` yet. That
  drafting step is explicitly deferred until the user has resolved
  whatever both agents surface.

## Open questions for Codex

This document is deliberately not a plan — the user asked for a jointly
produced plan, not a Claude-Code-authored one Codex reviews after the fact.
Starting points for the actual planning discussion (add more in
`discussion.md`, don't just answer inline here):

1. Does the customer-identity/session model need a new set of tables
   (e.g. `kefu_customer`, keyed by `external_userid`), or can `group_member`
   /`conversation_session` be generalized to cover both channels? Given
   Kefu has no "group," forcing it into `group_config`/`group_member` may
   be the wrong shape entirely.
2. How does a Kefu-sourced request eventually reach the right warehouse
   context (`warehouse_code`) without a `group_config.context` to read
   from, given there's no group?
3. What's the actual plan for section 4's notification gap — is it in
   scope for this migration at all, or a separate, later problem?
4. Should `docs/uchoice-design.md` (the original design doc) be revised in
   place, or should this be tracked as a clearly-marked delta/addendum
   the way Phase 1-4 were?
5. Staged rollout: does the existing internal Smart Robot flow need to keep
   running unmodified throughout (yes, per section 3.1), and does the new
   Kefu flow get built and tested end-to-end before any cutover, or does it
   ship service-by-service?
6. Verify the two "referenced only, not fetched" docs on Smart Robot's
   newer connection modes (101463, 101468) actually don't change anything
   in section 1 before fully committing — cheap to rule out, expensive to
   be wrong about.
