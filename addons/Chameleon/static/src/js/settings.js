/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { WebClient } from "@web/webclient/webclient";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

patch(WebClient.prototype, {
  /**
   * @override
   */
  setup() {
    super.setup();
    this.rpc = rpc;
    this.updateBranding();
  },

  async updateBranding() {
    let branding;
    try {
      branding = await this.rpc(
        "/web/dataset/call_kw/res.users/get_chameleon_public_branding",
        {
          model: "res.users",
          method: "get_chameleon_public_branding",
          args: [],
          kwargs: {},
        }
      );
    } catch {
      // Branding is optional. Keep Odoo defaults instead of interrupting startup.
      return;
    }

    this.title.setParts({ zopenerp: branding.website_title || "" });
    const backendIconEl = document.querySelector("link[rel~='icon']");
    if (backendIconEl && branding.image_1024) {
      backendIconEl.href = "data:image/png;base64," + branding.image_1024;
    }
  },
});
