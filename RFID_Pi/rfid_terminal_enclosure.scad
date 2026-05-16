/*
  ManageIO RFID terminal enclosure

  Target stack:
  - Raspberry Pi 4 Model B
  - Official Raspberry Pi 7-inch touchscreen
  - RC522/MFRC522 RFID reader behind the front-right tap area

  Usage:
  - Set part = "front", "rear", "fit_check", or "preview".
  - Export the selected part as STL from OpenSCAD.
  - Print fit_check first and adjust the parameters below before printing full parts.
*/

part = "preview"; // "front", "rear", "fit_check", "preview"

$fn = 36;

// Overall enclosure dimensions.
case_w = 285;
case_h = 155;
rear_depth = 42;
front_depth = 8;
wall = 3;
corner_r = 7;
fit_check_height = 4;

// Official 7-inch touchscreen opening and retaining lip.
display_visible_w = 155;
display_visible_h = 87;
display_bezel_clearance = 1.2;
display_outer_w = 194;
display_outer_h = 110;
display_frame_x = 14;
display_opening_y = 18;
display_lip = 5;

// Raspberry Pi 4 dimensions and mounting hole spacing.
pi_w = 85;
pi_h = 56;
pi_hole_dx = 58;
pi_hole_dy = 49;
pi_mount_x = 22;
pi_mount_y = 56;
standoff_d = 7;
standoff_h = 8;
screw_d = 2.8;

// RC522 reader. Adjust for the exact module used.
// AZ-Delivery RC522 kit board: 40 x 60 mm, 13.56 MHz, SPI, 3.3 V.
rc522_w = 40;
rc522_h = 60;
rc522_hole_dx = 34;
rc522_hole_dy = 54;
rc522_mount_x = 224;
rc522_mount_y = 48;
tap_area_d = 60;
rc522_standoff_h = rear_depth - 10;

// Thermal management for Raspberry Pi 4.
fan_grille_x = 64;
fan_grille_y = 86;
fan_grille_d = 34;
fan_mount_spacing = 24;
heat_slot_w = 24;
heat_slot_h = 4;

// Cable openings.
usb_c_cutout_w = 15;
usb_c_cutout_h = 8;
rj45_cutout_w = 18;
rj45_cutout_h = 16;
cable_exit_y = 28;

module rounded_box(size, r) {
    hull() {
        for (x = [r, size[0] - r])
            for (y = [r, size[1] - r])
                translate([x, y, 0])
                    cylinder(h = size[2], r = r);
    }
}

module screw_hole(h) {
    cylinder(h = h + 0.4, d = screw_d, center = false);
}

module standoff(x, y, h = standoff_h) {
    translate([x, y, wall])
        difference() {
            cylinder(h = h, d = standoff_d);
            translate([0, 0, -0.2])
                screw_hole(h + 0.4);
        }
}

module mount_pattern(origin_x, origin_y, dx, dy, h = standoff_h) {
    for (x = [0, dx])
        for (y = [0, dy])
            standoff(origin_x + x, origin_y + y, h);
}

module cable_tie_anchor(x, y, rot = 0) {
    translate([x, y, wall])
        rotate([0, 0, rot])
            difference() {
                cube([18, 8, 5], center = true);
                translate([0, 0, -0.2])
                    cube([11, 3, 6], center = true);
            }
}

module vent_slots(x, y, count, vertical = true) {
    for (i = [0 : count - 1]) {
        translate([x + (vertical ? 0 : i * 9), y + (vertical ? i * 9 : 0), -0.2])
            rounded_box([vertical ? 4 : 24, vertical ? 24 : 4, wall + 0.4], 2);
    }
}

module edge_vent_slots(y, z, count) {
    for (i = [0 : count - 1]) {
        translate([22 + i * 34, y, z])
            rounded_box([heat_slot_w, wall + 0.4, heat_slot_h], 2);
    }
}

module side_vent_slots(x, z, count) {
    for (i = [0 : count - 1]) {
        translate([x, 34 + i * 18, z])
            cube([wall + 0.4, 11, heat_slot_h]);
    }
}

module fan_grille() {
    translate([fan_grille_x, fan_grille_y, -0.2]) {
        cylinder(h = wall + 0.4, d = fan_grille_d);

        for (x = [-fan_mount_spacing / 2, fan_mount_spacing / 2])
            for (y = [-fan_mount_spacing / 2, fan_mount_spacing / 2])
                translate([x, y, 0])
                    cylinder(h = wall + 0.4, d = screw_d);
    }
}

module wall_keyhole(x, y) {
    translate([x, y, -0.2])
        union() {
            cylinder(h = wall + 0.4, d = 8);
            translate([-2.4, -18, 0])
                cube([4.8, 18, wall + 0.4]);
        }
}

module front_bezel() {
    opening_w = display_visible_w + display_bezel_clearance;
    opening_h = display_visible_h + display_bezel_clearance;
    display_x = display_frame_x + (display_outer_w - opening_w) / 2;
    display_y = case_h - display_opening_y - opening_h;

    difference() {
        rounded_box([case_w, case_h, front_depth], corner_r);

        // Screen visible opening.
        translate([display_x, display_y, -0.2])
            rounded_box([opening_w, opening_h, front_depth + 0.4], 3);

        // Shallow rear pocket for the touchscreen frame.
        translate([display_frame_x, case_h - display_opening_y - display_outer_h - display_lip, front_depth - 3])
            rounded_box([display_outer_w, display_outer_h, 4], 3);

        // Front-right badge zone. Keep this plastic-only: no metal behind it.
        translate([rc522_mount_x + rc522_w / 2, rc522_mount_y + rc522_h / 2, -0.2])
            cylinder(h = 1.2, d = tap_area_d);
        translate([rc522_mount_x + rc522_w / 2 - 12, rc522_mount_y + rc522_h / 2 - 5, -0.2])
            linear_extrude(height = 1.2)
                text("TAP", size = 10, font = "Liberation Sans:style=Bold");
    }
}

module rear_shell(shell_h = rear_depth) {
    difference() {
        rounded_box([case_w, case_h, shell_h], corner_r);

        translate([wall, wall, wall])
            rounded_box([case_w - 2 * wall, case_h - 2 * wall, shell_h], max(1, corner_r - wall));

        // Bottom cable exits: USB-C power and Ethernet.
        translate([case_w - 68, -0.2, cable_exit_y])
            cube([usb_c_cutout_w, wall + 0.4, usb_c_cutout_h]);
        translate([case_w - 42, -0.2, cable_exit_y - 3])
            cube([rj45_cutout_w, wall + 0.4, rj45_cutout_h]);

        // Rear service ventilation and optional 30 mm fan grille near the Pi.
        vent_slots(8, 55, 5, true);
        vent_slots(case_w - 12, 55, 5, true);
        fan_grille();

        // Bottom intake, top exhaust, and side vents create a passive chimney.
        edge_vent_slots(-0.2, 14, 7);
        edge_vent_slots(case_h - wall - 0.2, rear_depth - 13, 7);
        side_vent_slots(-0.2, 20, 5);
        side_vent_slots(case_w - wall - 0.2, 20, 5);

        // Wall mounting keyholes.
        wall_keyhole(38, case_h - 28);
        wall_keyhole(case_w - 38, case_h - 28);
        wall_keyhole(case_w / 2, 28);
    }

    // Mounting bosses. The RC522 is raised close to the front face for scan range.
    mount_pattern(pi_mount_x, pi_mount_y, pi_hole_dx, pi_hole_dy, standoff_h);
    mount_pattern(rc522_mount_x, rc522_mount_y, rc522_hole_dx, rc522_hole_dy, rc522_standoff_h);

    // Internal cable management.
    cable_tie_anchor(128, 45, 0);
    cable_tie_anchor(128, 72, 0);
    cable_tie_anchor(case_w - 52, 38, 90);

    // Raised keep-out guide around the RFID tap zone.
    translate([rc522_mount_x - 4, rc522_mount_y - 4, wall])
        difference() {
            cube([rc522_w + 8, rc522_h + 8, 2]);
            translate([4, 4, -0.2])
                cube([rc522_w, rc522_h, 2.4]);
        }
}

module fit_check() {
    intersection() {
        rear_shell(fit_check_height);
        cube([case_w, case_h, fit_check_height]);
    }
}

module preview_boards() {
    color("green")
        translate([pi_mount_x - 13.5, pi_mount_y - 3.5, wall + standoff_h + 1])
            cube([pi_w, pi_h, 1.6]);

    color("blue")
        translate([rc522_mount_x, rc522_mount_y, wall + rc522_standoff_h + 1])
            cube([rc522_w, rc522_h, 1.6]);

    color("black")
        translate([display_frame_x, case_h - display_opening_y - display_outer_h, rear_depth + 3])
            cube([display_outer_w, display_outer_h, 3]);
}

if (part == "front") {
    front_bezel();
} else if (part == "rear") {
    rear_shell();
} else if (part == "fit_check") {
    fit_check();
} else {
    rear_shell();
    translate([0, 0, rear_depth + 2])
        front_bezel();
    preview_boards();
}
