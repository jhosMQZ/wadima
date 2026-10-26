// Cola del robot recolector. Cada tarea corresponde a un evento horario real ya guardado en el servidor
// (evento «time.recorded»); el robot solo lo representa: nunca crea, cambia ni aprueba registros.

export interface RobotTask {
  eventId: number;
  kind: string;
  userId: string;
  userName: string;
}

export const ROBOT_KIND_LABEL: Record<string, string> = {
  clock_in: "entrada",
  clock_out: "salida",
  declared_close: "salida declarada",
  admin_close: "cierre por un aprobador",
  auto_close: "cierre automático",
  flag_pending: "jornada pendiente de cierre",
  approve: "aprobación",
  reject: "rechazo",
  duplicate_blocked: "intento de entrada duplicada",
};

/** Eventos en los que el robot va hasta el socio a recoger el registro; el resto se procesa en la estación. */
export const COLLECT_KINDS = new Set(["clock_in", "clock_out", "declared_close"]);

export class RobotQueue {
  private items: RobotTask[] = [];
  private seen = new Set<number>();

  constructor(private readonly max = 6) {}

  /** Encola sin duplicados (un mismo evento puede llegar dos veces tras reconectar). */
  push(task: RobotTask): boolean {
    if (this.seen.has(task.eventId)) return false;
    this.seen.add(task.eventId);
    if (this.seen.size > 500) this.seen = new Set([...this.seen].slice(-250));
    this.items.push(task);
    // Si se acumulan muchos, se descartan los más antiguos de la animación (el registro ya está guardado).
    while (this.items.length > this.max) this.items.shift();
    return true;
  }

  next(): RobotTask | undefined {
    return this.items.shift();
  }

  get size(): number {
    return this.items.length;
  }
}

export type RobotPhase = "idle" | "to_person" | "collecting" | "to_station" | "processing";
