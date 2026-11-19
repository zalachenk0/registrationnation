// ============================================================================
// File:         renewal_BV.ts
// Written by:   BV (Blas Vargas); CG (the per-step checks, tagged [CG])
// What it does: Checks that the required form fields are filled in before
//               printing, and on each step before Continue.
// Team-written: Logic written by BV (Blas Vargas); CG (lines tagged [CG]).
//               Outside code is used only through the third-party sources
//               listed below.
// Third-party:  none (standard library only)
// ============================================================================
export function displayDate(value: string): string {
  return value.replace(/^(\d{4})-(\d{2})-(\d{2})$/, "$2/$3/$1");
}

export function normalizeDate(value: string): string {
  return value.replace(/^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/, (_, month: string, day: string, year: string) =>
    `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`);
}

function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

// [CG] the same checks as before, split by form step, so Continue on each step can stop the customer at that step's blanks
// instead of letting them skip ahead and only find out at Review. renewalError runs all three, exactly as it did
export type FormStep = 1 | 2 | 3;
export const STEP_FIELDS: Record<FormStep, { personal?: string[]; license?: string[]; insurance?: string[] }> = {
  1: { personal: ["email", "phone", "firstName", "lastName", "dateOfBirth", "county", "address", "city", "state", "zip"] },
  2: { license: ["licenseNumber", "licenseState", "expirationDate", "licenseClass"] },
  3: { license: ["vehicleYear", "vehicleMake", "vehicleModel", "vin", "licensePlate"], insurance: ["provider", "policyNumber", "policyHolder", "endDate"] },
};

const values = (data: object, keys: string[]) => keys.map(key => String((data as Record<string, unknown>)[key] ?? ""));

// the first required field on this step that's still blank, so the screen can point at it
export function firstBlank(step: FormStep, personal: object, license: object, insurance: object): string {
  const groups = STEP_FIELDS[step];
  for (const [data, keys] of [[personal, groups.personal], [license, groups.license], [insurance, groups.insurance]] as const) {
    for (const key of keys ?? []) if (!String((data as Record<string, unknown>)[key] ?? "").trim()) return key;
  }
  return "";
}

export function stepError(step: FormStep, personal: object, license: object, insurance: object): string {
  if (firstBlank(step, personal, license, insurance)) {
    return step === 1 ? "Fill in every field marked * on this page before continuing."
      : step === 2 ? "Fill in every driver's license field marked * before continuing."
      : "Fill in every vehicle and insurance field marked * before continuing.";
  }
  if (step === 1) {
    const [email, phone, zip, birth] = values(personal, ["email", "phone", "zip", "dateOfBirth"]);
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return "Enter a valid email address.";
    if (phone.replace(/\D/g, "").length !== 10) return "Enter a 10-digit phone number.";
    if (!/^\d{5}(?:-\d{4})?$/.test(zip.trim())) return "Enter a 5-digit ZIP code or ZIP+4.";
    if (!validDate(birth)) return "Enter valid dates in MM/DD/YYYY format.";
  }
  if (step === 2) {
    const [expiration] = values(license, ["expirationDate"]);
    if (!validDate(expiration)) return "Enter valid dates in MM/DD/YYYY format.";
  }
  if (step === 3) {
    if (!/^[A-HJ-NPR-Z0-9]{17}$/i.test(String((license as { vin?: string }).vin ?? ""))) return "Enter a complete 17-character VIN before printing.";
    const [year] = values(license, ["vehicleYear"]);
    if (!/^\d{4}$/.test(year) || Number(year) < 1886 || Number(year) > new Date().getFullYear() + 1) return "Enter a valid four-digit vehicle year.";
    const [start, end, agentPhone] = values(insurance, ["startDate", "endDate", "agentPhone"]);
    if (![end, ...(start ? [start] : [])].every(validDate)) return "Enter valid dates in MM/DD/YYYY format.";
    if (start && end < start) return "The insurance end date must be on or after its start date.";
    if (agentPhone && agentPhone.replace(/\D/g, "").length !== 10) return "Enter a 10-digit agent phone number.";
  }
  return "";
}

// [BV] every check, before Review and Pay. [CG] now built from the per-step checks above. The "required" message wins
// whenever any step has a blank, like before, so Review can say which kind of problem it is
export function renewalError(personal: object, license: object, insurance: object): string {
  const steps: FormStep[] = [1, 2, 3];
  if (steps.some(step => firstBlank(step, personal, license, insurance))) return "Complete the required personal, license, vehicle and insurance fields. Use Back or Edit to fill the blanks.";
  for (const step of steps) {
    const error = stepError(step, personal, license, insurance);
    if (error) return error;
  }
  return "";
}
