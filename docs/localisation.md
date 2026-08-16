# Localising the desktop integration

AcornFS uses the gettext domain `acornfs` for the Nautilus menu, properties,
notifications, and Zenity dialogs. English remains the fallback when a
catalogue or individual translation is missing.

This first localisation boundary covers interface chrome. Technical values and
messages produced by the filesystem validation and repair core are not yet
catalogued, so the top-level backlog item remains open until those strings are
also translated.

## Update the template

Install GNU gettext, then run:

```bash
make messages
```

This regenerates `po/acornfs.pot` from the desktop-facing Python modules. Keep
format placeholders such as `{image}`, `{mountpoint}`, and `{count}` unchanged;
translators may reorder them. Preserve command names and environment variables
verbatim.

## Add a translation

Create or update a normal gettext PO file, for example `po/fr.po`, and compile
it into the package tree:

```bash
mkdir -p src/acornfs/locale/fr/LC_MESSAGES
msgfmt po/fr.po -o src/acornfs/locale/fr/LC_MESSAGES/acornfs.mo
```

Compiled `acornfs.mo` catalogues below `src/acornfs/locale` are included in
wheel and source packages. During development, `ACORNFS_LOCALE_DIR` may point
at another locale tree without modifying the installation.

Test both the extension and its detached dialogs from a session using that
locale. A translation is not release-ready until menu access keys, long
messages, 200 percent scaling, and a screen reader have also been checked on a
real supported GNOME desktop; those manual acceptance items remain separate in
the project backlog.
