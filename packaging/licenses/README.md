# Desktop runtime licenses

The Windows package includes these third-party runtime components. Their
licenses accompany the application in this directory. The application itself
is covered by the project `LICENSE` and `NOTICE.md` files.

- Python 3.13: the builder copies the license from its Python distribution into
  `Python.txt`.
- pyserial 3.5: [`pyserial.txt`](https://github.com/pyserial/pyserial/blob/v3.5/LICENSE.txt).
- Tcl 8.6: [`Tcl.txt`](https://github.com/tcltk/tcl/blob/core-8-6-15/license.terms).
- Tk 8.6: [`Tk.txt`](https://github.com/tcltk/tk/blob/core-8-6-15/license.terms).
- OpenSSL 3: [`OpenSSL.txt`](https://github.com/openssl/openssl/blob/openssl-3.0.18/LICENSE.txt).
- PyInstaller bootloader: the builder copies its license and distribution
  exception from the pinned PyInstaller package into `PyInstaller.txt`.

The Python distribution supplies its extension modules and supporting libraries.
If changing the Python distribution or the bundled runtime versions, review its
additional license notices before redistributing the resulting package.
