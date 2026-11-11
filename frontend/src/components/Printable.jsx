import { createPortal } from "react-dom";

/**
 * Renders ``children`` on screen and, when ``printable``, a second copy into #print-root
 * (outside the application, see index.html): the only thing the browser prints. Printing a
 * document therefore never includes the navigation, the map or the other panels.
 */
export default function Printable({ children, printable = true }) {
  const target = printable ? document.getElementById("print-root") : null;
  return (
    <>
      {children}
      {target && createPortal(children, target)}
    </>
  );
}
