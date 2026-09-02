# Test assets

Ten images. Each one has properties the task depends on, and getting them wrong
does not make a task fail: it makes the task stop measuring anything, which is
worse, because the run still produces a number.

Host them publicly over HTTPS. Committing them here and referencing the
`raw.githubusercontent.com` URL is the simplest option and has the side benefit
that anyone can reproduce a run.

| File | Used by | Must be |
| --- | --- | --- |
| `style-reference.jpg` | T01 | A **strongly stylised illustration**, not a photo, of something that is **not a market stall** |
| `street-summer.jpg` | T02 | A street scene with unmistakable summer cues and a **distinctive composition** |
| `shopfront.jpg` | T03 | A shopfront with a **legible sign above the door** and other detail around it |
| `person.jpg` | T08, T13 | One person, upper body, plain background, simple clothing |
| `jacket.jpg` | T08 | A jacket **on its own**, product shot, nobody wearing it |
| `sunglasses.jpg` | T13 | Sunglasses on their own, product shot |
| `ref1.jpg` .. `ref5.jpg` | T14 | Five images **distinguishable at a glance from each other** |

## Why each property matters

**T01, the style reference.** The whole task is whether the agent passes the
image or paraphrases it in words. If the reference is a photograph, "did the
style transfer" has no answer, because photographic is not a style you can see
carried across. Use something unmistakable: woodcut, risograph, flat vector,
cel-shaded, heavy impasto. And it must not depict a market stall, or the model
can satisfy the brief by copying the reference and you cannot tell the
difference.

**T02, the summer street.** Needs a composition you would notice losing. A
leading line, an off-centre landmark, an unusual camera height. A centred,
generic street makes "same framing" unjudgeable, and the judge check becomes a
coin flip.

**T03, the shopfront.** There must actually be a sign above a door, with text on
it, or the instruction is ambiguous and a failure is not the agent's fault. Other
detail around it matters too: without it, "everything else identical" has nothing
to be checked against.

**T14, the five references.** This is the one that will be got wrong. The task
exists to detect **silent truncation to four**, so the five images have to be
distinguishable at a glance in the output. Five similar landscapes make the
finding undetectable. Give each one an unmistakable element, ideally a distinct
dominant colour or an obviously different object, so that "were all five used"
is answerable by looking rather than by argument.

## Generate rather than source, for four of them

`person.jpg`, `jacket.jpg`, `sunglasses.jpg` and `style-reference.jpg` are better
generated with FLUX than downloaded.

- **No likeness question.** Publishing virtual try-on results performed on a real
  person's photograph is a consent question you do not need to have. A generated
  person removes it entirely.
- **No licence question**, for the same reason.
- **You control the properties.** The style reference can be made as
  distinctive as the task needs, rather than as distinctive as the stock library
  happened to be.

Say so in the writeup. Generating the fixtures with the system under test is a
detail people will notice, and it is honest about where the assets came from.

`street-summer.jpg` and `shopfront.jpg` are better as real photographs, because
editing real photographs is the actual use case. Unsplash and Pexels both permit
commercial use; credit them anyway, it costs a line.
