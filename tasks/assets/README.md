# Test assets

Ten images. Each one has properties the task depends on, and getting them wrong
does not make a task fail: it makes the task stop measuring anything, which is
worse, because the run still produces a number.

These are mirrored to a public repository, **djhoomin/first-try-assets**, and the
suite points at the `raw.githubusercontent.com` URLs there. They need a public
host because the image API fetches them itself, so a private repository would
block a run rather than merely hide it. Copies are kept here so the fixtures
travel with the tasks that describe them; if you change one, push both.

| File | Used by | Must be |
| --- | --- | --- |
| `style-reference.jpg` | T01 | A **strongly stylised illustration**, not a photo, of something that is **not a market stall** |
| `street-summer.jpg` | T02 | A street scene with unmistakable summer cues and a **distinctive composition** |
| `shopfront.jpg` | T03 | A shopfront with a **legible sign above the door** and other detail around it |
| `person.jpg` | T08, T13 | One person, upper body, plain background, simple clothing |
| `jacket.jpg` | T08 | A jacket **on its own**, product shot, nobody wearing it |
| `sunglasses.jpg` | T13 | Sunglasses on their own, product shot |
| `ref1.jpg` .. `ref5.jpg` | T14 | Five photo-realistic shots of **deliberately arbitrary objects**, one unmistakable marker each |

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
exists to detect **silent truncation to four**, so what you need is not
distinctiveness in general but *attribution*: after the blend, you have to be
able to say "that came from ref3" with nobody able to argue.

Ordinary photographs fail this. If ref3 is a red bicycle and a bicycle appears in
the output, you cannot separate "the model used ref3" from "the model drew a
bicycle because bicycles suit the scene", and the finding becomes invisible.

The property that fixes it is **arbitrariness**, not medium. Each reference needs
a marker the model would not plausibly invent unprompted. Photo-realistic product
shots of deliberately odd objects give you both a realistic medium and unambiguous
attribution:

1. A matte teal enamel teapot with a brass hexagonal handle
2. A fluorescent orange rotary telephone
3. A carved wooden owl with mismatched glass eyes, one green and one amber
4. A tall thermos striped in candy pink and black
5. A brass diving helmet with a cracked porthole

Five distinct dominant colours, five one-phrase descriptions, five things nothing
else would produce. Generate them.

This is an instrumented fixture rather than a representative one, and real users
blend brand assets and mood boards rather than novelty objects. Say that in the
writeup. Accepting artificiality to make a specific failure mode visible is a
normal methodological trade, and stating it plainly is what separates a benchmark
from a demo.

## Generate rather than source, for four of them

`person.jpg`, `jacket.jpg`, `sunglasses.jpg`, `style-reference.jpg` and all five
T14 references are better generated with FLUX than downloaded.

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
