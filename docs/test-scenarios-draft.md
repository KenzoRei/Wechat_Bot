# Test Scenarios — Draft Scratchpad

> This is a living scratchpad to capture test ideas as they come up during design.
> These will be formalized into a proper test plan in Phase 7.

---

## Collected During Phase 1

### Bot Classification
- [ ] User @bot with a valid authorized service request → bot starts info collection
- [ ] User @bot with a service they are not authorized for → bot replies with unauthorized message in Chinese
- [ ] User @bot with unrelated conversation ("hi, how are you?") → bot politely ends conversation in Chinese
- [ ] User sends message in group WITHOUT @bot → bot does not respond

### Info Collection Flow
- [ ] User provides all required fields in first message → bot skips prompting, goes straight to normalization + confirmation
- [ ] User provides partial fields → bot asks for the missing ones specifically
- [ ] User provides address as single line ("123 Main St New York NY 10001") → bot splits correctly into street/city/state/zip
- [ ] User confirms → request submitted, serial number returned
- [ ] User rejects confirmation → bot asks what to correct (or cancel)

### Access Control
- [ ] User exists in DB but not in this group → unauthorized reply
- [ ] User exists in this group but not for this service type → unauthorized reply
- [ ] Unknown WeChat openid (not in DB at all) → unauthorized reply

### Backend Result Notification
- [ ] Successful label creation → tracking number + PDF link returned to group
- [ ] Backend times out → customer notified in Chinese, Admin @mentioned with serial number
- [ ] Backend returns error → customer notified in Chinese, Admin @mentioned with serial number

### Idempotency
- [ ] WeChat retries same message (same wechat_msg_id) → processed exactly once, no duplicate label

### Edge Cases (add more as they come up)
- [ ] Customer walks away mid-flow and never confirms
- [ ] Two customers in same group start requests simultaneously
- [ ] Bot is @mentioned multiple times in one message

### Multi-thread / Concurrent Sessions (added Phase 2)
- [ ] User includes serial number in reply (clean format REQ-YYYYMMDD-NNNN) → backend regex matches, routes to correct session
- [ ] User references serial number casually ("那个001的单子，zip是10002") → Claude extracts intent, routes to correct session
- [ ] User has one active session, replies without serial number → backend routes by (user_id, group_id) automatically
- [ ] User has two active sessions, replies without serial number → Claude asks user to clarify which request
- [ ] User starts a new request while one is already active → Claude informs user of existing session, asks whether to continue or cancel
- [ ] Session expires (no updates within time limit) → status flips to timed_out, user notified, admin notified
- [ ] User replies to an expired session → bot informs user the session has expired, prompts to start a new request
