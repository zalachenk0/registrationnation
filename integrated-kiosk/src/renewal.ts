export function renewalError(personal: object, license: object, insurance: object): string {
  const values = (data: object, keys: string[]) => keys.map(key => String((data as Record<string, unknown>)[key] ?? ""));
  const required = [
    ...values(personal, ["firstName", "lastName", "dateOfBirth", "email", "phone", "address", "city", "state", "zip"]),
    ...values(license, ["licenseNumber", "licenseState", "expirationDate", "licenseClass", "vehicleYear", "vehicleMake", "vehicleModel", "vin", "licensePlate"]),
    ...values(insurance, ["provider", "policyNumber", "policyHolder", "endDate"]),
  ];
  if (required.some(value => !value.trim())) return "Complete the required personal, license, vehicle and insurance fields. Use Back or Edit to fill the blanks.";
  if (!/^[A-HJ-NPR-Z0-9]{17}$/i.test(String((license as { vin?: string }).vin ?? ""))) return "Enter a complete 17-character VIN before printing.";
  return "";
}
