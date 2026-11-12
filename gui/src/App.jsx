// .config/opencode/AGENTS.md APPLIED
// NutrientConflictsEvaluation Project AGENTS.md APPLIED
import { useEffect, useState } from "react";
import { brandConflicts, evaluateFood, health, searchFoods } from "./api.js";

function PairTable({ title, rows, tone }) {
  if (!rows || rows.length === 0) {
    return (
      <section className={`panel ${tone}`}>
        <h3>{title}</h3>
        <p className="muted">None identified.</p>
      </section>
    );
  }
  return (
    <section className={`panel ${tone}`}>
      <h3>{title}</h3>
      <table>
        <thead>
          <tr>
            <th>Pair</th>
            <th>Strength / evidence</th>
            <th>Quantity</th>
            <th>Mechanism</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.nutrient_a}-${row.nutrient_b}`} className={row.highlighted ? "antagonistic" : ""}>
              <td>
                <strong>{row.pair}</strong>
                {row.highlighted ? <span className="badge">antagonistic</span> : null}
              </td>
              <td>
                {row.strength} / {row.evidence_level}
              </td>
              <td>
                <div>{row.quantity_a}</div>
                <div>{row.quantity_b}</div>
              </td>
              <td>{row.mechanism}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Warning({ warning }) {
  if (!warning || !warning.required) return null;
  return (
    <section className="warning">
      <h3>Warning</h3>
      <p>{warning.text}</p>
      {warning.quantities?.length ? (
        <ul>
          {warning.quantities.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      {warning.next_steps?.length ? (
        <ol>
          {warning.next_steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

function Identity({ product }) {
  return (
    <section className="panel identity">
      <h2>
        {product.brand} — {product.description}
      </h2>
      <dl>
        <div><dt>fdc_id</dt><dd>{product.fdc_id}</dd></div>
        <div><dt>brand_owner</dt><dd>{product.brand_owner}</dd></div>
        <div><dt>food_category_id</dt><dd>{product.food_category_id || "-"}</dd></div>
        <div><dt>data_type</dt><dd>{product.data_type || "-"}</dd></div>
      </dl>
      <h3>Nutrient list</h3>
      <div className="chips">
        {(product.nutrient_list || []).map((item) => (
          <span key={item} className="chip">{item}</span>
        ))}
        {!product.nutrient_list?.length ? <span className="muted">none derived</span> : null}
      </div>
    </section>
  );
}

function ProductView({ product }) {
  return (
    <>
      <Identity product={product} />
      <div className="grid">
        <PairTable title="Synergistic" rows={product.synergistic} tone="synergy" />
        <PairTable title="Antagonistic" rows={product.antagonistic} tone="antagonism" />
      </div>
      <section className="panel summary">
        <h3>Summary</h3>
        <p>{product.summary?.statement}</p>
        <p className="muted">
          synergistic {product.summary?.counts?.synergistic ?? 0} · antagonistic{" "}
          {product.summary?.counts?.antagonistic ?? 0} · context dependent{" "}
          {product.summary?.counts?.context_dependent ?? 0}
        </p>
      </section>
      <Warning warning={product.warning} />
      <p className="disclaimer">{product.disclaimer}</p>
    </>
  );
}

function BrandView({ payload }) {
  return (
    <>
      <section className="panel summary">
        <h2>{payload.brand_owner}</h2>
        <p>{payload.summary?.statement}</p>
        <p className="muted">
          {payload.total_products} product(s) · page {payload.page} of {payload.pages}
        </p>
      </section>
      <Warning warning={payload.warning} />
      {(payload.products || []).map((product) => (
        <details key={product.fdc_id} open={payload.products.length <= 2}>
          <summary>
            {product.description} <span className="muted">fdc_id {product.fdc_id}</span>
          </summary>
          <ProductView product={product} />
        </details>
      ))}
      <section className="panel">
        <h3>Brand rollup</h3>
        <table>
          <thead>
            <tr><th>Relation</th><th>Pair</th><th>Products with both</th><th>Max %DV A</th><th>Max %DV B</th></tr>
          </thead>
          <tbody>
            {(payload.aggregate?.interactions || []).map((row) => (
              <tr key={`${row.nutrient_a}-${row.nutrient_b}`} className={row.highlighted ? "antagonistic" : ""}>
                <td>{row.relation}</td>
                <td>{row.pair}</td>
                <td>{row.co_occurring_products}</td>
                <td>{row.dv_percent_a ?? "-"}</td>
                <td>{row.dv_percent_b ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <p className="disclaimer">{payload.disclaimer}</p>
    </>
  );
}

export default function App() {
  const [mode, setMode] = useState("fdc");
  const [value, setValue] = useState("");
  const [result, setResult] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState(null);

  useEffect(() => {
    health().then(setStatus).catch(() => setStatus(null));
  }, []);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setCandidates([]);
    setResult(null);
    if (!value.trim()) return;
    setLoading(true);
    try {
      if (mode === "fdc") {
        setResult({ kind: "product", data: await evaluateFood(Number(value)) });
      } else {
        setResult({ kind: "brand", data: await brandConflicts(value, 1, 10) });
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function search() {
    setError("");
    setResult(null);
    setLoading(true);
    try {
      const payload = await searchFoods(value);
      setCandidates(payload.items || []);
      if (!payload.items?.length) setError("No foods matched that text.");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <header>
        <h1>Nutrient Conflicts Evaluation</h1>
        <p className="muted">
          Synergistic and antagonistic nutrient combinations in branded foods.
          {status ? ` ${status.app} ${status.version}` : ""}
        </p>
      </header>
      <form onSubmit={submit} className="search">
        <div className="modes">
          <label>
            <input type="radio" checked={mode === "fdc"} onChange={() => setMode("fdc")} /> fdc_id
          </label>
          <label>
            <input type="radio" checked={mode === "brand"} onChange={() => setMode("brand")} /> brand_owner
          </label>
        </div>
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={mode === "fdc" ? "e.g. 1105905" : "e.g. CAMPBELL SOUP COMPANY"}
        />
        <button type="submit" disabled={loading}>{loading ? "Working..." : "Evaluate"}</button>
        <button type="button" className="secondary" onClick={search} disabled={loading || mode !== "brand"}>
          Find products
        </button>
      </form>
      {error ? <div className="error">{error}</div> : null}
      {candidates.length ? (
        <section className="panel">
          <h3>Matches</h3>
          <ul className="candidates">
            {candidates.map((item) => (
              <li key={item.fdc_id}>
                <button
                  type="button"
                  onClick={() => {
                    setMode("fdc");
                    setValue(String(item.fdc_id));
                    setCandidates([]);
                    evaluateFood(item.fdc_id)
                      .then((data) => setResult({ kind: "product", data }))
                      .catch((err) => setError(err.message));
                  }}
                >
                  {item.fdc_id}
                </button>{" "}
                {item.brand_owner} — {item.description}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {result?.kind === "product" ? <ProductView product={result.data} /> : null}
      {result?.kind === "brand" ? <BrandView payload={result.data} /> : null}
    </main>
  );
}
