/*
id: technician_appointment_context
title: Technician and appointment context of a work order
purpose: Puts the appointment of a work order next to everything that can conflict with it: the technician's other orders on the same day, technician and service-shop off-work periods overlapping the appointment, and the technician's capacity slots. Use for "appointment overlaps", "technician unavailable but scheduled", "cannot book slot", "double booking", "appointment at wrong time".
param: TenantId | int | required | Catalog.Tenant.Id (every Sirius table is tenant scoped; always supply it)
param: WorkOrderId | int | required | Operation.WorkOrder.Id
regions: EU, APAC
tables: Operation.WorkOrder, Catalog.TechnicianOffWork, Catalog.ServiceShopOffWork, Catalog.TechnicianCapacity, Catalog.TimeSlot
pii: Detail:free_text_body
interpret: Kind WORKORDER is the order itself (StartDate/EndDate = AppointmentDate/AppointmentEndDate, EmployeeId = assigned technician; both NULL = no appointment set). TECH_OTHER_WORKORDER = other orders of the same technician on the same calendar day (overlapping times = double booking). TECH_OFFWORK / SHOP_OFFWORK = active off-work rows overlapping the appointment (Detail = comment): the order is booked while the technician or shop is off. TECH_CAPACITY = configured capacity per time slot (Detail = slot and capacity); no row = no capacity configured for the technician, which blocks online booking. Times are stored in UTC; Catalog.Tenant.ShowUTCHour is the display offset.
next: wo_header, wo_status_timeline, wo_field_changes
*/
WITH w AS (
    SELECT wo.Id, wo.EmployeeId, wo.ServiceShopId, wo.AppointmentDate, wo.AppointmentEndDate, wo.StatusId
    FROM Operation.WorkOrder wo
    WHERE wo.Id = @WorkOrderId AND wo.TenantId = @TenantId
)
SELECT 'WORKORDER' AS Kind, w.Id AS RefId, w.AppointmentDate AS StartDate, w.AppointmentEndDate AS EndDate,
       w.StatusId, w.EmployeeId, CAST(NULL AS nvarchar(300)) AS Detail
FROM w
UNION ALL
SELECT 'TECH_OTHER_WORKORDER', o.Id, o.AppointmentDate, o.AppointmentEndDate, o.StatusId, o.EmployeeId, NULL
FROM w
JOIN Operation.WorkOrder o ON o.EmployeeId = w.EmployeeId AND o.TenantId = @TenantId AND o.Id <> w.Id
 AND CAST(o.AppointmentDate AS date) = CAST(w.AppointmentDate AS date)
UNION ALL
SELECT 'TECH_OFFWORK', t.Id, t.StartDate, t.EndDate, NULL, t.TechnicianId, LEFT(t.Comment, 300)
FROM w
JOIN Catalog.TechnicianOffWork t ON t.TechnicianId = w.EmployeeId AND t.Active = 1
 AND t.StartDate <= COALESCE(w.AppointmentEndDate, w.AppointmentDate) AND t.EndDate >= w.AppointmentDate
UNION ALL
SELECT 'SHOP_OFFWORK', s.Id, s.StartDate, s.EndDate, NULL, NULL, LEFT(s.Comment, 300)
FROM w
JOIN Catalog.ServiceShopOffWork s ON s.ServiceShopId = w.ServiceShopId AND s.Active = 1
 AND s.StartDate <= COALESCE(w.AppointmentEndDate, w.AppointmentDate) AND s.EndDate >= w.AppointmentDate
UNION ALL
SELECT 'TECH_CAPACITY', c.Id, NULL, NULL, NULL, c.TechnicianId,
       'slot ' + COALESCE(ts.TimeSlot, '?') + ' capacity ' + CAST(c.Capacity AS varchar(10))
FROM w
JOIN Catalog.TechnicianCapacity c ON c.TechnicianId = w.EmployeeId AND c.Active = 1
LEFT JOIN Catalog.TimeSlot ts ON ts.Id = c.TimeSlotId
ORDER BY Kind, StartDate
