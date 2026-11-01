<#macro content>
  <#-- Native UsernamePasswordForm cancellation retains Keycloak's action/session checks. -->
  <#if pageId?? && (pageId == "login" || pageId == "login-password")>
    <form action="${url.loginAction}" method="post">
      <button type="submit" name="cancel" value="true" id="stockpilot-return"
              class="${properties.kcButtonSecondaryClass!} ${properties.kcButtonBlockClass!}">
        ${msg("stockpilotReturnToOrganization")}
      </button>
    </form>
  </#if>
</#macro>
