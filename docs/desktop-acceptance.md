# GNOME desktop acceptance

Run this checklist in a clean Ubuntu 24.04 amd64 session with GNOME/Nautilus
46 or later. Automated tests cover the menu model, dialog command contract and
the kernel FUSE operations beneath common file-manager workflows. They cannot
establish visual layout, assistive-technology behaviour or Nautilus's own
drag-and-drop and trash integration.

Record the AcornFS commit, Ubuntu, GNOME, Nautilus, Zenity and Orca versions,
display scale, theme and outcome. Do not include private image paths or image
contents in public evidence.

## Preparation

1. Install the current branch using the user-install procedure and restart
   Files explicitly.
2. Create a generated BeebSCSI fixture with `acornfs create-beebscsi`; do not
   use irreplaceable media or private images.
3. Keep a terminal open and record `acornfs status --json` before and after the
   session.
4. Run `make test-live` on the same host first. This must pass the copy, move,
   delete, atomic-save, writable-unmount and recovery cases.

## Keyboard and assistive technology

- Navigate to the DAT or DSC using only the keyboard, open the context menu,
  enter **Acorn FS Support**, and invoke every action offered for that image.
- Confirm that focus is visible, menu names and descriptions are announced,
  Tab and Shift+Tab reach every dialog control, Enter activates the stated
  primary action, and Escape or **Cancel** makes no change.
- With Orca enabled, verify the title, prompt, field label, progress state,
  destructive warning, error detail and completion status of creation,
  validation, repair, recovery, mount-location and Greaseweazle dialogs.
- Confirm that a non-repairable validation report has one **Close** action and
  that repair and recovery choices are not announced as equivalent actions.

## Visual matrix

Repeat creation, validation with findings, repair confirmation and recovery at:

| Theme | Window/display condition |
| --- | --- |
| Light | normal width and 100 percent scale |
| Dark | normal width and 100 percent scale |
| Light | narrow usable desktop area |
| Dark | 200 percent scale |

No control, warning or progress value may be clipped; long translated text must
remain readable without making the dialog larger than the usable desktop.

## Nautilus file workflows

On a writable mount, use Files rather than terminal commands to verify:

1. Drag a host file into the mount and copy a mounted file back to the host.
2. Copy and move files with the clipboard, including movement between mounted
   directories.
3. Rename a file and a populated directory, then permanently delete both.
4. Edit an existing file in a GNOME editor that uses temporary-file replacement
   and confirm the replacement survives clean unmount and remount.
5. Attempt **Move to Trash**. Record whether Nautilus offers it; if offered, it
   must either complete coherently or fail with an accurate, non-destructive
   message. Do not claim trash support merely from permanent-delete coverage.
6. Open image and mounted-file properties and compare them with `acornfs inspect`.

Unmount from the grouped menu, verify that the sidebar entry disappears, then
run `acornfs validate` and confirm there is no pending recovery. Any crash,
hang, lost metadata, silent sanitisation, ambiguous dialog or stale mount is a
release blocker.
