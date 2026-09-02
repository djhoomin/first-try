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
first-try run --resources none     # hide the server's MCP resources
first-try run --resume             # skip tasks already recorded
first-try fetch --stdio "..."      # resolve pending generations into images
first-try review                   # contact sheet for the judgement calls
```

Generation is job-shaped: a call returns `{"status": "pending", "request_id": ...}`
and the render exists minutes later, so a run records receipts rather than
pictures. `fetch` asks the server what became of each job and writes the media
URLs back into the transcripts. Run it after a run, give the renders a few
minutes, and run it again for anything still pending. Everything it calls is
free.

## What the suite costs to run

Two bills, and they are easy to confuse. The **image API** bill is what the
report measures. The **model** bill is what it costs to drive the agent, and it
is usually the larger of the two.

The runner caches the system prompt, the tool schemas and the growing
conversation prefix. Without that, an agent that reads a long skill guide on
turn one pays for it again on every later turn, and the cost of the suite scales
with how thorough the agent is. The report prints the cache hit rate; if it is
low, something is invalidating the prefix.

The default runner is `claude-sonnet-5`. A frontier model is not obviously the
right subject anyway: the interesting question is whether a *typical* agent gets
the platform right, not whether the most capable one can. Use `--model` to
compare, and expect a larger model to cost several times more per task.

## Judging the rest

Roughly half the suite ends in a question no assertion can answer: did the style
transfer, is the composition intact, is the text spelled correctly, were all the
references used. Those carry the most information and are the easiest to skip.

`first-try review` builds a single page holding every outstanding question with
the prompt, the call and the images beside it. Checks marked `manual` never
auto-pass; they hold a task at `review` until a person answers them.

## Interruptions

Results are written after **every** task, not at the end of the suite. A run
that is interrupted, times out, or has its machine put to sleep keeps everything
it already earned, and `--resume` picks up from there without re-running or
re-paying for what is already recorded.

Calls time out (`--call-timeout`, 300s by default) rather than blocking
forever, because a sleeping laptop kills the connection behind the bridge
without erroring: nothing fails, the response simply never arrives. If a call
does time out the suite stops there instead of grinding through the remaining
tasks to prove the transport is still dead.

## Resources

MCP servers publish **resources** as well as tools, and servers often put their
model catalogue there. But the model-facing tool APIs have no native notion of a
resource, and most clients surface them to the human rather than the model.

A benchmark that silently ignores them measures a client that cannot read the
server's own documentation, which makes any conclusion about capability
discovery unsupported. So by default the server's resources are offered to the
model as two extra tools, `list_resources` and `read_resource`, and the report
says so. `--resources none` reproduces the stricter reading. The mode is
recorded on every transcript, because a discoverability score means a different
thing under each.

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
