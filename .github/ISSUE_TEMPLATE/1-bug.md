---
name: Bug
about: Something is not working as designed
labels: '#Bug'
---

<!--
This tool reads a live board, so its characteristic failure is output that looks
completely normal but rests on a wrong premise. "How does it fail?" is the field
that separates an urgent bug from an annoying one, so please pick honestly.
-->

### What happens

<!-- The actual behaviour, with real numbers or output where you have them. -->

### What you expected instead

### How to reproduce

1.
2.
3.

### Where it happens

<!-- Tick one. -->

- [ ] fetch (reading from GitHub)
- [ ] normalize (deriving stats, bins, rows)
- [ ] build (writing the HTML)
- [ ] report page (chart, table, notes)
- [ ] report form
- [ ] settings (accounts and projects)
- [ ] CLI
- [ ] not sure

### How does it fail?

<!-- Tick one. Silent wrongness is more urgent than a visible error. -->

- [ ] It errors or crashes visibly
- [ ] It produces output that looks fine but is wrong
- [ ] It silently omits data
- [ ] Something looks wrong visually

### Commit or version

<!-- git rev-parse --short HEAD -->
