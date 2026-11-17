export const providers = [
  "Allstate", "American Family", "Farm Bureau", "Farmers", "GEICO", "Liberty Mutual", "Mercury",
  "Nationwide", "Progressive", "State Farm", "The Hartford", "Travelers", "USAA",
];

export const counties = [
  "Bastrop", "Bell", "Bexar", "Blanco", "Brazoria", "Burnet", "Caldwell", "Collin", "Comal",
  "Dallas", "Denton", "El Paso", "Fort Bend", "Galveston", "Guadalupe", "Harris", "Hays",
  "Hidalgo", "Kendall", "Kerr", "Medina", "Montgomery", "Nueces", "Tarrant", "Travis",
  "Webb", "Williamson", "Wilson",
];

const models: Record<string, string[]> = {
  Acura: ["Integra", "MDX", "RDX", "TLX"],
  Audi: ["A3", "A4", "A5", "A6", "Q3", "Q5", "Q7", "Q8"],
  BMW: ["2 Series", "3 Series", "4 Series", "5 Series", "X1", "X3", "X5"],
  Chevrolet: ["Blazer", "Camaro", "Colorado", "Equinox", "Malibu", "Silverado", "Suburban", "Tahoe", "Traverse", "Trax"],
  Dodge: ["Challenger", "Charger", "Durango", "Journey"],
  Ford: ["Bronco", "Edge", "Escape", "Expedition", "Explorer", "F-150", "F-250", "Fusion", "Maverick", "Mustang", "Ranger"],
  GMC: ["Acadia", "Canyon", "Sierra", "Terrain", "Yukon"],
  Honda: ["Accord", "Civic", "CR-V", "Fit", "HR-V", "Odyssey", "Passport", "Pilot", "Ridgeline"],
  Hyundai: ["Elantra", "Kona", "Palisade", "Santa Fe", "Sonata", "Tucson", "Venue"],
  Jeep: ["Cherokee", "Compass", "Gladiator", "Grand Cherokee", "Renegade", "Wrangler"],
  Kia: ["Forte", "K5", "Niro", "Seltos", "Sorento", "Soul", "Sportage", "Telluride"],
  Lexus: ["ES", "GX", "IS", "NX", "RX", "UX"],
  Mazda: ["Mazda3", "Mazda6", "CX-30", "CX-5", "CX-50", "CX-9", "CX-90", "MX-5 Miata"],
  "Mercedes-Benz": ["A-Class", "C-Class", "E-Class", "GLA", "GLC", "GLE", "S-Class"],
  Nissan: ["Altima", "Frontier", "Kicks", "Maxima", "Murano", "Pathfinder", "Rogue", "Sentra", "Titan", "Versa"],
  Ram: ["1500", "2500", "3500", "ProMaster"],
  Subaru: ["Ascent", "Crosstrek", "Forester", "Impreza", "Legacy", "Outback", "WRX"],
  Tesla: ["Model 3", "Model S", "Model X", "Model Y", "Cybertruck"],
  Toyota: ["4Runner", "Camry", "Corolla", "Highlander", "Prius", "RAV4", "Sequoia", "Sienna", "Tacoma", "Tundra"],
  Volkswagen: ["Atlas", "Golf", "Jetta", "Passat", "Taos", "Tiguan"],
};

export const makes = Object.keys(models);

export function vehicleModels(make: string): string[] {
  const match = makes.find(option => option.toLowerCase() === make.trim().toLowerCase());
  return match ? models[match] : [];
}
