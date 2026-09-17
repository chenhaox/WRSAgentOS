# Built-in skill library

The explicit registry in wrs_agent/skills.py is the executable contract. Metadata includes
parameters, node binding, capabilities, resources, preconditions, verification, timeout
and allowed recovery. Functions do not import Zenoh, a model SDK or WRS.

Use lookup_skills(goal, current_capabilities, bindings) to obtain candidates. Chinese
aliases and tags rank candidates; ranking never executes a function or grants permission.
The Runtime validates every step against current capabilities before starting the plan.
A WRS FK-only profile cannot acquire objects, even if the registry describes pick.

To add a skill, add its argument schema, ordinary function, SkillSpec and explicit node
binding in configs/bindings.toml. Implement and verify the capability on that node.
Do not load Python paths proposed by a model. This file is explanatory; SkillSpec remains
the single machine-readable contract.
