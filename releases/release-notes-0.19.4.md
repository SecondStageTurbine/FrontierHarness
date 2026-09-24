Frontier 0.19.4: fixes from a full code review

- Security: an agent in the file sandbox can no longer use Frontier's own API to switch its sandbox off or run a command outside it.
- Automations whose project or agent was removed stop creating empty sessions and no longer hold up other automations; removing a project removes its automations.
- A queued message or a rename made while a turn runs is no longer lost when the turn changes agent.
- Rewinding a resumed Claude conversation no longer leaves Claude answering its old last message.
- Team mode: a review asking to fix a task that does not exist no longer fails the turn, and later tasks start with earlier tasks' changes.
- An agent with one login that runs out of usage is rested instead of chosen again first, and a conversation that merely mentions "quota" is no longer mistaken for running out.
