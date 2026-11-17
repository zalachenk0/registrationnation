// ============================================================================
// File:         renewal_BV.ts
// Written by:   BV (Blas Vargas)
// What it does: Checks that the required form fields are filled in before
//               printing.
// Team-written: Logic written by BV (Blas Vargas). Outside code is used only
//               through the third-party sources listed below.
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

export function renewalError(personal: object, license: object, insurance: object): string {
  const values = (data: object, keys: string[]) => keys.map(key => String((data as Record<string, unknown>)[key] ?? ""));
  const required = [
    ...values(personal, ["firstName", "lastName", "dateOfBirth", "email", "phone", "address", "city", "state", "zip", "county"]),
    ...values(license, ["licenseNumber", "licenseState", "expirationDate", "licenseClass", "vehicleYear", "vehicleMake", "vehicleModel", "vin", "licensePlate"]),
    ...values(insurance, ["provider", "policyNumber", "policyHolder", "endDate"]),
  ];
  if (required.some(value => !value.trim())) return "Complete the required personal, license, vehicle and insurance fields. Use Back or Edit to fill the blanks.";
  if (!/^[A-HJ-NPR-Z0-9]{17}$/i.test(String((license as { vin?: string }).vin ?? ""))) return "Enter a complete 17-character VIN before printing.";
  const [email, phone, zip, birth] = values(personal, ["email", "phone", "zip", "dateOfBirth"]);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return "Enter a valid email address.";
  if (phone.replace(/\D/g, "").length !== 10) return "Enter a 10-digit phone number.";
  if (!/^\d{5}(?:-\d{4})?$/.test(zip.trim())) return "Enter a 5-digit ZIP code or ZIP+4.";
  const [year, expiration] = values(license, ["vehicleYear", "expirationDate"]);
  if (!/^\d{4}$/.test(year) || Number(year) < 1886 || Number(year) > new Date().getFullYear() + 1) return "Enter a valid four-digit vehicle year.";
  const [start, end, agentPhone] = values(insurance, ["startDate", "endDate", "agentPhone"]);
  if (![birth, expiration, end, ...(start ? [start] : [])].every(validDate)) return "Enter valid dates in MM/DD/YYYY format.";
  if (start && end < start) return "The insurance end date must be on or after its start date.";
  if (agentPhone && agentPhone.replace(/\D/g, "").length !== 10) return "Enter a 10-digit agent phone number.";
  return "";
}
