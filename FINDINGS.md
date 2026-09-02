# Would an agent get FLUX right on the first try?

I built a benchmark to find out, and pointed it at the official FLUX MCP server.

The premise is that an MCP server is a user interface whose user is a language
model. Every other kind of interface gets usability testing. Agent-facing ones
get opinions. So: fifteen realistic creative tasks, run cold through an agent
against `mcp.bfl.ai`, on three models from two labs, with no documentation beyond what the server itself
exposes, scored on tool selection, first-call validity, constraint compliance,
recovery, cost discipline and discoverability.

The harness is [first-try](https://github.com/djhoomin/first-try). FLUX is a good
subject rather than a soft one: the server is well built, the pricing is
published, and media generation has the failure modes that make agent ergonomics
genuinely hard. Calls are slow, job-shaped, and expensive enough that a wrong one
costs real money.

**The headline is that it does well.** Nearly everything I designed a task to
catch, it handled. There is one real gap, and it is the same one in every place
it shows up.

## The finding: cost is never expressed as a number

An agent working on this platform can discover everything about what it is able
to do, and nothing about what anything costs.

`get_credits` returns a balance and a free-tier quota. `bfl://models` is a
complete, well-written catalogue: every model with its capabilities, its
`max_input_images`, its recommended uses, and a `tier` of `budget`, `balanced`,
`premium` or `specialized`. Cost appears only as adjectives. "Fast and
affordable." "Fastest and most affordable." There is no price field, and no tool
estimates the cost of a call before it is made.

Two tasks show what that produces.

Asked directly *"what can you actually do with images here, and what will it cost
me?"*, the agent gave an accurate and complete account of the capabilities, with
no hallucinated model names, and **no prices at all**. It answered the half of
the question the platform can answer.

Asked for four twenty-second clips at the highest quality available, it did three
things right before committing: checked the balance, read two skill guides, then
called. It was not careless. The balance was **954 credits**. The call costs
**2,320 credits**, four clips times twenty seconds times $0.29/s, entirely inside
the documented envelope with no confirmation step. It had no way to learn it was
committing to more than twice the account.

Afterwards it described what it had spent as *"4 of your 5 free video
generations"*, because quota units are the only cost vocabulary available to it.
No monetary figure appears anywhere in its answer.

**Claude Opus, Claude Sonnet and Gemini 3.7 Flash all behaved the same way.** All
three committed the call, none of them named a price.

Gemini closes the argument. Asked what the platform costs, it did the most
thorough thing available: it checked the balance, listed the server's resources,
and read the model catalogue. Its answer reports "Paid Credit Balance: 738.6
credits" and contains no monetary figure of any kind. An agent that finds the one
document describing every model still cannot say what a single call costs,
because the document does not contain that information.

**Suggested fix.** A `price` field alongside `tier` in each `bfl://models` entry
would cost nothing to add and would let an agent answer the question at all.
Better still, return an estimated cost on the pending response of a generating
call, so the number arrives at the moment of commitment rather than needing to be
looked up in advance.

## A design note: agents read tools, not resources

Across fifteen tasks Claude called `get_skill` **13 times** and `read_resource`
**once**. Gemini reached for resources more readily. But the pattern that holds
across all three models, on both labs, is sharper than either count:

**every resource read happened in the one task that asks the agent to describe
the platform, and none happened while it was working.**

Skills are exposed as **tools**, so a model reaches for them the way it reaches
for anything else. `bfl://models` is exposed as an MCP **resource**, and the
model-facing tool APIs have no native notion of one. Every agent here was handed
explicit tools to list and read resources, which is more access than most clients
provide, and all three still treated the catalogue as reference material for
answering questions about the platform rather than as something to consult while
using it.

I have no evidence of harm. The tasks I expected this to break, it did not break.
But `bfl://models` is where the per-model reference limits and the draft-first
video workflow live, and it is worth knowing that agents read it when asked what
the platform does and not when deciding how to do something. Anything an agent
must act on is safer in a skill guide or a tool description.

## Two documentation notes

**The prose names a parameter the schema does not use.** The documentation calls
reference images `input_image`; the schema takes `input_medias`, an array of
`{url}` or `{id}` objects. I know this one matters because I wrote my own checks
from the prose, and they were wrong, and it cost me a full run and three false
failures before I looked at an actual call.

**Four live tools are absent from the published tool table.** `get_skill`,
`list_skills`, `get_result` and `request_upload_url` are all real and all used.
`get_skill` is the most-called tool in the entire suite and appears nowhere in
the documented list of seven.

## What went right, which is nearly all of it

Constraint compliance was six out of six. Unprompted, the agent chose
`flux2_flex` for typography, `flux2_max` for a hero shot, and a klein variant for
a throwaway batch, without being told which model suits which job.

It routed to the `vto` tool even when the prompt deliberately said "use the vto
model", which is the exact trap the documentation warns about. Asked to change
one sign on a busy shopfront, it changed the sign and nothing else. Told it was
still working out the motion for a clip, it rendered a draft rather than a full
pass, and did so on all three models. Given an underspecified brief, Claude asked
a question instead of spending money, though Gemini guessed. Before attempting a five-image blend it read the
multi-reference guide first.

The skills system, in short, is good and gets used heavily.

## What I got wrong

More than the platform did, and this is the part worth reading.

**I expected the headline to be silent truncation.** Five reference images went
into a blend, four came out, and the agent asserted all five were used. But
`bfl://models` declares `max_input_images: 8` for that model, so five was well
inside the limit and the interface dropped nothing. One reference is
under-represented in the output. That is a model observation, not an interface
defect, and I downgraded it.

**I nearly published a finding that draft mode was being skipped.** Two models
appeared to render a fifteen-second clip at full quality when asked for a rough
look. Both had in fact set `draft: true`, and a third model later did the same. My check read `draft` at the top level
of the call while the interface also accepts it on each request, which is where
both models put it. My own cost estimator read both locations, which is how the
call was priced correctly at $0.90 while the check called it a failure.

**Six of the failures across these runs were defects in the harness**, not in
FLUX. Checks written from prose rather than schema. A recovery metric that could
never pass in a dry run, because the harness itself blocks the calls it then
waits to see succeed. A first-tool assertion that punished the agent for reading
the documentation before acting. A task primed with request ids I had invented,
which the agent detected and reported. A task that rewarded asking a clarifying
question while another punished it. And the draft check above.

Every one of them produced a plausible, well-formatted, entirely false finding.
If there is a transferable lesson here it is that a benchmark's first results are
mostly about the benchmark, and that the interesting work is the second pass.

## Limitations

**One run per task, and only four tasks on the third model.** Agent behaviour is
stochastic and this measures a single sample. Treat an individual result as an anecdote. The cost finding is stated
with confidence only because it reproduced across three models from two labs, in
three separate tasks.

**Image costs are lower bounds.** FLUX.2 bills by megapixel and the published
table quotes floor prices, so real spend is at least what is reported. Video
figures are exact.

**Resource exposure is a methodological choice.** I gave the agent explicit tools
to list and read MCP resources, which most clients do not. That is the generous
reading of discoverability.

**The judgement calls are judgements.** Whether a style transferred or a
composition survived is not a measurement. Those checks are excluded from every
score rather than dressed up as one.

## In short

FLUX's MCP server gets the hard parts right: routing, model selection, targeted
editing, draft discipline, and knowing when to ask rather than spend. It stood up
to tasks written specifically to break it.

The gap is cost. An agent can discover everything about this platform except what
it costs, which leaves it unable to answer a question users ask constantly and
unable to protect them from a decision it cannot price. That is fixable with a
field in a JSON document rather than with engineering, which is the best kind of
finding to be left holding.
