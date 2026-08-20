# Cycle 8 audit

286 accepted, 31 topics, 242 cued. All 286 audited by hand.

## The verifier ran

18 paid on-demand calls, `purpose="item_verification"`, in `cost_log`. It
rejected 16 items with specific reasons, for example:

    Ohne Hinweis ist die Lücke nicht eindeutig, da neben 'haben' auch
    'hatten' (Plusquamperfekt) grammatisch und semantisch passend waere.

    Ohne Hinweis passen auch andere Dativpronomen wie mir, uns oder ihnen
    ebenso gut in den Satz.

The lane fix worked. Month-to-date spend is $0.27 against the 5 EUR ceiling.

## Defects: 7 of 286, 2.4 percent

**One bad carrier, three items.** `Tennisschlüssel` is not a word anyone
means. Both halves are real German, so no dictionary or compound check can
reject it. It reached `kasus_akkusativ_formen`, `verb_praesens_regelm` and
`relativsatz_nom_akk`. This is the class left open on purpose last cycle and
it is exactly what the verifier should have caught. It did not.

**A capitalisation trap, four items.** `TypoGrader` strict-fails a
capitalisation mismatch, by design, so that `sie` and `Sie` stay distinct.
Two items hand the learner a cue in the wrong case:

    Sie kochen oft zusammen mit ___ (ihre) Familie ...      -> Ihrer
    Ihr Schwiegersohn repariert ___ (ihr) Stuhl ...         -> Ihren

A learner who follows the cue writes `ihrer` and is marked wrong. Two more
put a lowercase cue on a sentence-initial gap:

    ___ (alt) Batterien können im Supermarkt abgegeben werden.  -> Alte
    ___ (letzter) Woche hatte ich plötzlich fiese Bauchschmerzen. -> Letzte

Fix: capitalise the cue whenever the answer is capitalised. One line, and it
closes all four.

## The rest is clean

279 of 286. Every determiner and adjective item now carries a working cue and
has exactly one answer:

    Nach ___ (die) Arbeit treffe ich oft meine Nachbarin im Park.  -> der
    Wir bewunderten ... aber wir vermissten einen ___ (groß) Kleiderschrank.  -> großen

Zero cue-equals-answer. Zero duplicate prompts. Every cue round-trips.
`Tablett` is gone. Every one of last cycle's 14 named defects is gone from the
output except the compound.

Three cues are inflected forms rather than base forms (`letzter`, `nächster`,
`leckerer`) because that is what the lemmatiser returns. They are still
solvable. `leckerer` can be misread as a comparative.

## Trend

| | C6 | C7 | **C8** |
|---|---|---|---|
| Accepted | 419 | 370 | **286** |
| Topics | 38 | 36 | **31** |
| Audited | 333 of 419 | 370 of 370 | **286 of 286** |
| Defects | 8.4% | 3.8% | **2.4%** |
| Unsolvable by typing | not measured | 45% | **0** |
| Verifier ran | no | no | **yes** |

## What the count cost

Five topics dropped to zero: `artikel_bestimmt_nom`, `zustandspassiv`,
`futur_i`, `verben_reflexiv_dat`, `perfekt_sein`. Four of those went to zero
because every item they produced last cycle was defective and is now correctly
rejected. `artikel_bestimmt_nom` is different: a nominative article slot is
its own citation form, so no cue can make it solvable by typing. It cannot be
fixed by this mechanism.

31 of 49 topics carry items. That is a coverage question for the scheduler,
not a defect in what shipped.

## Two things I could not settle from the output

The verifier is not self-consistent. It rejected two relative-pronoun items
because `welche` would also fit, while keeping thirteen items of the same
shape. It rejected several `haben`/`hatten` items for Perfekt versus
Plusquamperfekt ambiguity while keeping eight of the same shape. Its
judgements are correct where it fires; it does not fire every time.

The `not_run` count is not recoverable from the output files. The console
prints it and the script now exits non-zero if it is above zero.
