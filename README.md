# first-try

A usability benchmark for agent-facing APIs.

An MCP server is a user interface whose user is a language model. Every other
kind of interface gets usability testing. This one gets opinions.

`first-try` runs a fixed suite of realistic tasks through an agent against an MCP
server, with cold context and no documentation beyond what the server itself
exposes, and measures what happens: which tool it reached for, whether the call
validated, whether it honoured the constraint, how many turns it needed to
recover from an error, and what it spent getting there.

The output is a scorecard and a list of failures specific enough to fix.

## Why an image API first

The suite ships pointed at [FLUX](https://docs.bfl.ai), because it is a good
test case rather than a soft one: the MCP server is
[open source](https://github.com/black-forest-labs/flux-mcp), the pricing is
published per model and per second, and media generation has the failure modes
that make agent ergonomics hard. Calls are slow, job-shaped, and expensive
enough that a wrong one costs real money.

Nothing in the harness is FLUX-specific except the price table and the tasks.

## Install

```bash
pip install -e ".[claude,dev]"
```

## Run it dry first

Always. A dry run executes the free tools, blocks every billable call, and still
scores the suite, because what is being measured is the call the agent chose to
make.

```bash
first-try run --stdio "npx -y mcp-remote https://mcp.bfl.ai" --dry-run
```

Then a live run with a ceiling:

```bash
first-try run --stdio "npx -y mcp-remote https://mcp.bfl.ai" --budget 5.00 --per-call-cap 1.00
```

The FLUX server is hosted and OAuth-only, so there is nothing to run locally.
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote) bridges it to stdio,
opens a browser for sign-in on first use, and caches tokens in `~/.mcp-auth`.
You will need a BFL account with credits; every call is billed to the
organisation you pick during sign-in, which is why the dry run comes first.

`--http` is there for servers that accept static credentials, with `--header`
for your own auth.

```bash
first-try tasks                    # list the suite
first-try run --only T09,T11,T12   # the three that cost nothing
```

## How spending is controlled

Three layers, because a benchmark that can empty an account is not a benchmark.

- **`--dry-run`** blocks every billable call.
- **`--per-call-cap`** blocks individual expensive calls while cheap ones run, so
  a mostly-live run can still contain a task that must never execute.
- **`--budget`** is the ceiling for the whole run.

A task may narrow the run policy and may never widen it. One task in the shipped
suite is marked `force_dry_run` and cannot be made to execute from the command
line at all.

**Blocked calls return a synthetic success, not an error.** This is deliberate.
Returning an error would measure how the agent recovers from the safety rail
rather than how it uses the platform, and recovery has its own tasks that use
real server errors. The blocked call is fully recorded, and the report counts it
as *intended* spend, because the agent still meant to make it.

## What it measures

| Axis | Question |
| --- | --- |
| Tool selection | Did it reach for the right tool? |
| First-call validity | Did the call validate on the first attempt? |
| Constraint compliance | Did it honour what was asked, without being told how? |
| Recovery | After a failure, how many turns to a working call? |
| Cost discipline | What did it spend against the cheapest correct path? |
| Discoverability | Can it answer what the platform does without reading docs? |
| Outcome quality | Did the result satisfy the brief? |

## Writing tasks

Tasks are YAML: a user message, a spend policy, and a list of checks. Nothing
about expected behaviour lives in Python, so the suite is reviewable by people
who will never open the harness.

```yaml
- id: T05
  title: Typography
  axes: [constraint_compliance, outcome_quality]
  prompt: >-
    Make a poster for a night market. The words 'NIGHT MARKET' and 'Fridays 6pm'
    have to be readable and spelled correctly.
  checks:
    - kind: arg_equals
      tool: generate_image
      path: requests[*].model
      value: flux2_flex
    - kind: spend_at_most
      value: 0.10
    - kind: manual
      note: is the rendered text correct
```

Check kinds are in `src/first_try/checks.py`. Paths support indexing and
wildcards (`requests[*].model`). A `manual` check never auto-passes: it marks a
task as needing review rather than quietly claiming a result.

Replace the `https://assets.invalid/...` URLs in `tasks/suite-v1.yaml` with your
own images before running.

## Limitations

Worth reading before quoting any number from this.

- **Image costs are lower bounds.** FLUX.2 bills by megapixel and the published
  table quotes floor prices, so real spend is at least what is reported. Video
  figures are exact.
- **One run per task.** Agent behaviour is stochastic and this measures a single
  sample, so treat a single task result as an anecdote and the aggregate as the
  signal. Repeated sampling is the obvious next version.
- **Outcome quality is the weak axis.** Whether an image satisfies a brief is a
  judgement, and judgements from a model are not measurements. Those checks are
  marked `manual` and excluded from the pass rate rather than dressed up.
- **The suite encodes one person's view** of what a professional user needs from
  a generative image API. That view is arguable. Arguing with it by editing the
  YAML is the intended use.

## Licence

MIT.
