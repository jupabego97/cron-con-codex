import { expect, test } from "@playwright/test";

test("acceso inválido, API caída, recuperación y catálogo vacío", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Contraseña").fill("wrong");
  await page.getByRole("button", { name: "Ingresar", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByLabel("Contraseña").fill("browser-test-password");
  await page.getByRole("button", { name: "Ingresar", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Hoy: qué necesita atención" }),
  ).toBeVisible();
  await page.route("**/api/v1/operations/products?*", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Fallo controlado de prueba" }),
    }),
  );
  await page.getByRole("button", { name: "Productos", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "Fallo controlado de prueba",
  );
  await page.unroute("**/api/v1/operations/products?*");
  await page
    .getByLabel("Buscar nombre o referencia")
    .fill("no-such-product-zzzz");
  await expect(
    page.getByText("Sin registros para este criterio.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("login → consultas reales → captura auditada de los módulos", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", (response) => {
    if (response.url().includes("/api/v1/") && response.status() >= 500)
      errors.push(`${response.status()} ${response.url()}`);
  });
  await page.goto("/");
  await page.getByLabel("Contraseña").fill("browser-test-password");
  await page.getByRole("button", { name: "Ingresar", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Hoy: qué necesita atención" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Productos", exact: true }).click();
  await page
    .getByRole("button", { name: "Computador de prueba", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Indicadores", exact: true }),
  ).toBeVisible();
  await page
    .getByText("Ciclo comercial y primera disponibilidad", { exact: true })
    .click();
  await page.getByLabel("Ciclo", { exact: true }).selectOption("on_request");
  await page
    .getByRole("button", { name: "Guardar perfil", exact: true })
    .click();
  await expect(
    page.getByText("Guardado correctamente.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Pedidos", exact: true }).click();
  await page.getByRole("button", { name: "PO", exact: true }).click();
  await page.getByLabel("Estado", { exact: true }).selectOption("confirmed");
  await page.getByRole("button", { name: "Guardar seguimiento" }).click();
  await expect(
    page.getByText("Guardado correctamente.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Caja", exact: true }).click();
  await page.getByText("Certificar saldo de cierre", { exact: true }).click();
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Bogota",
  }).format(new Date());
  await page.getByLabel("Fecha de cierre", { exact: true }).fill(today);
  await page
    .getByLabel("Saldo real al cierre", { exact: false })
    .fill("5000000");
  await page
    .getByRole("button", { name: "Certificar saldo", exact: true })
    .click();
  await expect(
    page.getByText("Guardado correctamente.", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Servicio técnico", exact: true })
    .click();
  await page
    .getByText("Recibir equipo / registrar garantía", { exact: true })
    .click();
  await page.getByLabel("Cliente", { exact: true }).fill("Cliente navegador");
  const device = `Equipo ${testInfo.project.name} ${Date.now()}`;
  await page.getByLabel("Equipo / modelo", { exact: true }).fill(device);
  await page.getByLabel("Falla reportada", { exact: true }).fill("No enciende");
  await page.getByLabel("Recibido el", { exact: true }).fill(today);
  await page
    .getByRole("button", { name: "Recibir equipo", exact: true })
    .click();
  await page.getByRole("button", { name: device, exact: true }).click();
  await expect(
    page.getByText("Falla: No enciende", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Estado", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Estado y recuperación" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Ventas", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Ventas", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".error")).toHaveCount(0);
  expect(errors).toEqual([]);
});
