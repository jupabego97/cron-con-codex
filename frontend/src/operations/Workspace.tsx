import { lazy, Suspense } from "react";
import { Filters } from "../api";

const Today = lazy(() => import("./Today"));
const Products = lazy(() => import("./Products"));
const Receiving = lazy(() => import("./Receiving"));
const Treasury = lazy(() => import("./Treasury"));
const Repairs = lazy(() => import("./Repairs"));
const System = lazy(() => import("./System"));

export default function Workspace({
  tab,
  filters,
  navigate,
}: {
  tab: string;
  filters: Filters;
  navigate: (tab: string, product?: string) => void;
}) {
  return (
    <Suspense fallback={<p>Cargando módulo…</p>}>
      {tab === "today" && <Today navigate={navigate} />}
      {tab === "product-workspace" && (
        <Products
          filters={filters}
          selected={filters.product_key}
          navigate={navigate}
        />
      )}
      {tab === "receiving" && <Receiving />}
      {tab === "treasury" && <Treasury currency={filters.currency || "COP"} />}
      {tab === "repairs" && <Repairs filters={filters} />}
      {tab === "system" && <System />}
    </Suspense>
  );
}
