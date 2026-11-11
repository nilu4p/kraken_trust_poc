import Printable from "./Printable.jsx";

/**
 * A report as the chain of command reads it: marking, message header, numbered paragraphs.
 * ``printable`` also renders it into #print-root (outside the app), the only thing printed.
 */
function Body({ header, sections, seal }) {
  return (
    <article className="report-paper">
      <p className="report-marking">{header.classification}</p>
      <h1 className="report-title">{header.title}</h1>
      <dl className="report-header">
        {[
          ["Réf.", header.ref],
          ["GDH", header.dtg],
          ["Origine", header.originator],
          ["Rédacteur", header.author],
          ["Destinataires", header.addressees || "—"],
          ["Objet", header.subject],
          ...(header.period ? [["Période", header.period]] : []),
        ].map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {sections.map((section, index) => (
        <section key={section.title} className="report-section">
          <h2>
            {index + 1}. {section.title}
          </h2>
          {section.rows?.length > 0 && (
            <dl className="report-rows">
              {section.rows.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          )}
          {section.paragraphs?.map((text, i) => (
            <p key={i} className="report-paragraph">
              {text}
            </p>
          ))}
          {section.table &&
            (section.table.rows?.length ? (
              <table className="report-table">
                <thead>
                  <tr>
                    {section.table.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {section.table.rows.map((r, i) => (
                    <tr key={i}>
                      {r.map((cell, j) => (
                        <td key={j}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="report-paragraph">{section.table.empty || "Néant."}</p>
            ))}
        </section>
      ))}
      {seal && (
        <footer className="report-seal">
          <p>SHA-256 : {seal.sha256}</p>
          <p>{seal.signed ? `Signé ECDSA — certificat ${seal.cert_fingerprint?.slice(0, 32)}…` : "Non signé (PKI indisponible à l'émission)"}</p>
        </footer>
      )}
      <p className="report-marking">{header.classification}</p>
    </article>
  );
}

export default function ReportDocument({ header, sections, seal, printable = false }) {
  if (!header) return null;
  return (
    <Printable printable={printable}>
      <Body header={header} sections={sections || []} seal={seal} />
    </Printable>
  );
}
