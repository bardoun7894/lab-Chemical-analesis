# Certificate feedback — client meeting 2026-09-05

Source: screen-share walkthrough of the three certificate forms (warranty,
work test, MTC). Client paused mid-list to revise ("توقف، خليني أعدل هذي"),
so this is what was said before the pause. Treat as not-yet-final.

## What was reviewed

All three certificates were opened from `/certificates`, then the warranty
certificate was opened from the list of already-issued certificates and put
into print preview. The client's comments are about the shared behaviour of
all three forms, not about the warranty alone.

## Requests

### R1 — pick pipes from more than one order (all three certificates)

Today the form takes exactly one order (`order_id`), lists that order's pipes,
and you tick the ones the certificate covers. The client wants to tick pipes
belonging to **several orders** on one certificate: "multi orders".

Affects: `app/routes/certificates.py` (`order_id` is a single int today, and
`Certificate.production_order_id` is a single FK), the picker in
`app/templates/certificates/form.html`, and the material-details grouping in
`_print_base.html`.

Open question for the client: what does the certificate then print in the
"order" field — every order number, or the customer only?

### R2 — quantity/length must be the sum of the ACTUAL pipe lengths

On the printed warranty the "Pipe Length" showed 6 m, which is neither the
standard length nor anything the client chose. Two pipes were ticked.

What the client wants: the certificate reads the **actual** length recorded on
each ticked pipe and prints their **sum** as the quantity/length, rather than a
standard or a per-pipe figure. Standard length was mentioned as 12 m, so a
hard-coded or standard-derived 6 m is wrong twice over.

Affects: the length column in `_print_base.html` and whatever fills it in
`certificate_service` / `certificates.py`.

### Explicitly out of scope for now

The certificate header/title: "ممكن نخليها زي ما هي" — leave it as it is.

## Status

Implemented 2026-09-05 on `feat/certificates-round-2` — see specs/014-certificates/README.md "Round 2". (Original note: Captured, not implemented. Client said stop while they revise the list, so
wait for the revised version before building R1 (it changes the data model).)
